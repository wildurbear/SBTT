#!/usr/bin/env python3
"""
sbtext -- edit Stellar Blade's in-game text by hand.

This is a thin helper around `repak` and a small `.locres` reader. It does not
write any text for you: you edit plain CSV files in a spreadsheet or a text
editor, and this tool turns them into a mod pak the game can load.

Workflow:
    ./tools/sbtext.py doctor          check prerequisites (run this first)
    ./tools/sbtext.py extract         pull Game.locres out of the game's pak
    ./tools/sbtext.py export          write work/export/<locale>/<namespace>.csv
    ./tools/sbtext.py build           edits/*.csv -> dist/zzz_text_P.pak
    ./tools/sbtext.py install         copy the pak into the game's ~mods/
    ./tools/sbtext.py uninstall       remove it again
    ./tools/sbtext.py status          what's extracted / built / installed

Set SB_GAME_DIR if the game is not at the default Steam location.
"""
import argparse
import csv
import glob
import io
import os
import re
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from locres import LocRes  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORK = os.path.join(REPO, 'work')
DIST = os.path.join(REPO, 'dist')
EDITS = os.path.join(REPO, 'edits')

GAME_DIR = os.environ.get(
    'SB_GAME_DIR',
    os.path.expanduser('~/.local/share/Steam/steamapps/common/StellarBladeDemo'))
PAKS = os.path.join(GAME_DIR, 'SB', 'Content', 'Paks')
SOURCE_PAK = os.path.join(PAKS, 'pakchunk0-WindowsNoEditor.pak')
MODS = os.path.join(PAKS, '~mods')

LOC_ROOT = 'SB/Content/Localization/Game'
EXTRACT = os.path.join(WORK, 'extract')
SEQ_NS = 'StringTable_Seq'   # subtitles; the default when a CSV omits `namespace`

PAK_NAME = 'zzz_text_P.pak'   # _P suffix is mandatory; see docs/04-build-install.md
PAK_VERSION = 'V11'
PATH_HASH_SEED = str(0xD6054A3A)   # matches pakchunk0; repak wants decimal


def show(path):
    """Repo-relative when it makes sense, absolute otherwise."""
    p = os.path.abspath(path)
    return os.path.relpath(p, REPO) if p.startswith(REPO + os.sep) else p


def run(cmd):
    """Run a subprocess, keeping its output in order with ours."""
    sys.stdout.flush()
    subprocess.run(cmd, check=True)


def locres_path(locale):
    return os.path.join(EXTRACT, LOC_ROOT.replace('/', os.sep), locale, 'Game.locres')


def need_extract(locale='en'):
    if not os.path.exists(locres_path(locale)):
        sys.exit("no extracted locres -- run `sbtext.py extract` first")
    return locres_path(locale)


# ---------------------------------------------------------------- ordering

def sort_key(key):
    """Scene, then beat, then trailing numbers *numerically*.

    Subtitle keys look like Seq_Subtitle_<SCENE>_<Beat>_<NN>, sometimes with an
    extra numeric segment (DED01_FindFusionDrive_01_02). Plain alphabetical
    sorting puts _10 before _2, which makes the reference CSV painful to read.
    """
    if key.startswith('Seq_Actor_'):
        return (0, '', '', ())
    body = re.sub(r'^Seq_(Subtitle_)?', '', key)
    parts = body.split('_')
    scene, nums, tail = parts[0], [], []
    for p in reversed(parts[1:]):
        if p.isdigit() and not tail:
            nums.append(int(p))
        else:
            tail.append(p)
    return (1, scene, '_'.join(reversed(tail)), tuple(reversed(nums)))


def scene_of(key):
    if key.startswith('Seq_Actor_'):
        return 'ACTOR'
    m = re.match(r'Seq_Subtitle_([A-Za-z0-9]+?)_', key)
    if m:
        return m.group(1)
    m = re.match(r'Seq_(ME)_', key)
    return m.group(1) if m else 'OTHER'


# ---------------------------------------------------------------- commands

def cmd_extract(args):
    if not os.path.exists(SOURCE_PAK):
        sys.exit(f'game pak not found: {SOURCE_PAK}\n'
                 f'set SB_GAME_DIR to your install location')
    os.makedirs(WORK, exist_ok=True)
    shutil.rmtree(EXTRACT, ignore_errors=True)
    run(['repak', 'unpack', SOURCE_PAK, '-i', LOC_ROOT, '-o', EXTRACT])
    found = sorted(os.path.basename(os.path.dirname(p))
                   for p in glob.glob(os.path.join(EXTRACT, LOC_ROOT.replace('/', os.sep),
                                                   '*', 'Game.locres')))
    print(f'extracted {len(found)} locales: {", ".join(found)}')


def cmd_export(args):
    """One CSV per namespace, so each kind of text can be browsed on its own."""
    path = need_extract(args.locale)
    lr = LocRes(path)
    wanted = [n.strip() for n in args.namespace.split(',')] if args.namespace else None
    outdir = args.output or os.path.join(WORK, 'export', args.locale)
    os.makedirs(outdir, exist_ok=True)

    total = 0
    for _, ns, entries in sorted(lr.namespaces, key=lambda x: -len(x[2])):
        if wanted and ns not in wanted:
            continue
        rows = [(e[1], lr.strings[e[3]]) for e in entries]
        is_seq = ns == SEQ_NS
        rows.sort(key=lambda r: sort_key(r[0]) if is_seq else (1, r[0], '', ()))
        cols = ['namespace', 'key']
        if is_seq:
            cols.append('scene')
        cols += ['orig_len', 'original', 'replacement']
        out = os.path.join(outdir, f'{ns}.csv')
        with open(out, 'w', newline='', encoding='utf-8') as f:
            w = csv.writer(f)
            w.writerow(cols)
            for k, v in rows:
                row = [ns, k] + ([scene_of(k)] if is_seq else []) + [len(v), v, '']
                w.writerow(row)
        print(f'  {len(rows):5d}  {show(out)}')
        total += len(rows)

    if not total:
        sys.exit(f'no matching namespaces (asked for {args.namespace})')
    print(f'exported {total} rows across {args.locale}')


def load_replacements(paths):
    """Merge (namespace, key) -> replacement from CSVs. Later files win.

    A `namespace` column is optional; without one, rows are assumed to be
    subtitles, which keeps subtitle-only CSVs short.
    """
    edits, seen = {}, {}
    for p in paths:
        try:
            text = open(p, encoding='utf-8-sig').read()
        except UnicodeDecodeError:
            sys.exit(f'{show(p)}: not a text CSV -- is this an extracted game file?')
        with io.StringIO(text) as f:
            reader = csv.DictReader(f)
            fields = reader.fieldnames or []
            for required in ('key', 'replacement'):
                if required not in fields:
                    sys.exit(f'{show(p)}: needs a `{required}` column, got {fields}')
            for row in reader:
                k = (row.get('key') or '').strip()
                v = row.get('replacement') or ''
                if not k or not v.strip():
                    continue
                ns = (row.get('namespace') or '').strip() or SEQ_NS
                ident = (ns, k)
                if ident in edits and edits[ident] != v:
                    print(f'  note: {k} overridden by {os.path.basename(p)} '
                          f'(was set in {seen[ident]})')
                edits[ident] = v
                seen[ident] = os.path.basename(p)
    return edits


def cmd_build(args):
    src = need_extract('en')
    paths = args.input or sorted(glob.glob(os.path.join(EDITS, '*.csv')))
    if not paths:
        sys.exit(f'no CSVs to build in {show(EDITS)}/ '
                 f'(put your edited CSVs there, or pass them with -i)')
    print('reading: ' + ', '.join(show(p) for p in paths))

    edits = load_replacements(paths)
    if not edits:
        sys.exit('no non-empty `replacement` values found; nothing to build')

    lr = LocRes(src)
    known = {(ns, k): v for ns, k, v, _ in lr.items()}
    unknown = [f'{ns}/{k}' for (ns, k) in edits if (ns, k) not in known]
    if unknown:
        sys.exit(f'{len(unknown)} key(s) not found, first few: {unknown[:5]}\n'
                 f'check the `namespace` column -- it defaults to {SEQ_NS}')

    warnings = 0
    for (ns, k), new in sorted(edits.items()):
        orig = known[(ns, k)]
        is_actor = k.startswith('Seq_Actor_')
        if is_actor and not re.match(r'^<[^>]+>.*</>$', new):
            print(f'  WARN {k}: speaker tag lost its <Name>...</> wrapper')
            warnings += 1
        # Only subtitles are time-boxed by a voice clip; menu and item text
        # is free to be longer than the original.
        if ns == SEQ_NS and len(new) > len(orig) * 1.5 and len(new) > len(orig) + 20:
            print(f'  WARN {k}: {len(orig)} -> {len(new)} chars; subtitle duration '
                  f'follows the voice clip, so this will likely be cut off')
            warnings += 1
        if not is_actor and re.search(r'<[^>]+>', orig) and not re.search(r'<[^>]+>', new):
            print(f'  WARN {ns}/{k}: original had <markup> tags, replacement has none')
            warnings += 1
    by_ns = {}
    for (ns, _) in edits:
        by_ns[ns] = by_ns.get(ns, 0) + 1
    print(f'{len(edits)} replacements across {len(by_ns)} namespace(s): '
          + ', '.join(f'{n} {c}' for n, c in sorted(by_ns.items())))
    print(f'{warnings} warning(s)')
    if warnings and not args.force:
        print('(warnings are advisory; pass --force to silence this note)')

    build_dir = os.path.join(WORK, 'pak')
    shutil.rmtree(build_dir, ignore_errors=True)
    for loc in [l.strip() for l in args.locales.split(',') if l.strip()]:
        lp = locres_path(loc)
        if not os.path.exists(lp):
            sys.exit(f'locale {loc!r} not extracted')
        loc_lr = LocRes(lp)
        applied = sum(1 for (ns, k), v in edits.items() if loc_lr.set(ns, k, v))
        out = os.path.join(build_dir, LOC_ROOT.replace('/', os.sep), loc, 'Game.locres')
        os.makedirs(os.path.dirname(out), exist_ok=True)
        loc_lr.save(out)
        print(f'  {loc}: applied {applied}/{len(edits)}')

    os.makedirs(DIST, exist_ok=True)
    pak = os.path.join(DIST, PAK_NAME)
    run(['repak', 'pack', build_dir, pak,
         '--version', PAK_VERSION, '-p', PATH_HASH_SEED])
    print(f'built {show(pak)} ({os.path.getsize(pak):,} bytes)')
    if args.install:
        cmd_install(args)


def cmd_install(args):
    pak = os.path.join(DIST, PAK_NAME)
    if not os.path.exists(pak):
        sys.exit('nothing built yet -- run `sbtext.py build` first')
    os.makedirs(MODS, exist_ok=True)
    shutil.copy(pak, MODS)
    print(f'installed -> {os.path.join(MODS, PAK_NAME)}')


def cmd_uninstall(args):
    target = os.path.join(MODS, PAK_NAME)
    if os.path.exists(target):
        os.remove(target)
        print(f'removed {target}')
    else:
        print('nothing installed')


REPAK_TESTED = '0.2.3'
REPAK_HELP = """
  repak is a single self-contained binary -- no runtime, no package needed.

    1. Download the build for your OS from
       https://github.com/trumank/repak/releases
    2. Put it on your PATH (on Linux/macOS:
       mkdir -p ~/.local/bin && mv repak ~/.local/bin/ && chmod +x ~/.local/bin/repak)
    3. Make sure that folder is on your PATH.

  Or, if you have Rust: cargo install repak_cli
"""


def cmd_doctor(args):
    """Check everything needed to build and install, before anything is run."""
    ok = True

    def check(label, passed, detail='', fix=''):
        nonlocal ok
        print(f'  [{"OK" if passed else "!!"}] {label}' + (f'  {detail}' if detail else ''))
        if not passed:
            ok = False
            if fix:
                print(fix.rstrip('\n'))
        return passed

    print('dependencies')
    check('python3', sys.version_info >= (3, 7),
          f'{sys.version_info.major}.{sys.version_info.minor}')

    repak = shutil.which('repak')
    if check('repak', bool(repak), repak or 'not found on PATH', REPAK_HELP):
        try:
            out = subprocess.run(['repak', '--version'], capture_output=True,
                                 text=True, timeout=10).stdout.strip()
            ver = out.split()[-1] if out else '?'
            note = f'{out}' + ('' if ver == REPAK_TESTED
                               else f'   (tested against {REPAK_TESTED}; '
                                    f'flags have changed between versions)')
            print(f'       {note}')
        except Exception as e:
            print(f'       could not read version: {e}')

    print('game')
    check('install dir', os.path.isdir(GAME_DIR), GAME_DIR,
          '       set SB_GAME_DIR to the folder containing SB/')
    check('source pak', os.path.exists(SOURCE_PAK),
          os.path.basename(SOURCE_PAK) if os.path.exists(SOURCE_PAK) else SOURCE_PAK)

    mods_parent = os.path.dirname(MODS)
    writable = os.path.isdir(mods_parent) and os.access(mods_parent, os.W_OK)
    check('~mods writable', writable, MODS,
          '       check permissions on the Paks folder')

    print('workspace')
    extracted = os.path.exists(locres_path('en'))
    check('extracted locres', extracted,
          'work/extract/...' if extracted else 'run `sbtext.py extract`')

    print()
    print('ready to build' if ok else 'not ready -- fix the [!!] items above')
    sys.exit(0 if ok else 1)


def cmd_status(args):
    print(f'game dir : {GAME_DIR}'
          f'{"" if os.path.isdir(GAME_DIR) else "   [MISSING]"}')
    locales = sorted(os.path.basename(os.path.dirname(p)) for p in
                     glob.glob(os.path.join(EXTRACT, LOC_ROOT.replace('/', os.sep),
                                            '*', 'Game.locres')))
    print(f'extracted: {len(locales)} locales' + (f' ({", ".join(locales)})' if locales else ''))
    exports = glob.glob(os.path.join(WORK, 'export', '*', '*.csv'))
    print(f'exported : {len(exports)} namespace csv(s)')
    csvs = sorted(glob.glob(os.path.join(EDITS, '*.csv')))
    edits = load_replacements(csvs) if csvs else {}
    ns_used = sorted({ns for ns, _ in edits})
    print(f'edits    : {len(csvs)} csv(s), {len(edits)} line(s)'
          + (f' in {", ".join(ns_used)}' if ns_used else ''))
    pak = os.path.join(DIST, PAK_NAME)
    print(f'built    : {"yes, " + format(os.path.getsize(pak), ",") + " bytes" if os.path.exists(pak) else "no"}')
    inst = os.path.join(MODS, PAK_NAME)
    print(f'installed: {"yes" if os.path.exists(inst) else "no"}  ({MODS})')


p = argparse.ArgumentParser(description=__doc__,
                            formatter_class=argparse.RawDescriptionHelpFormatter)
sub = p.add_subparsers(dest='cmd', required=True)

s = sub.add_parser('extract', help='pull Game.locres out of the game pak')
s.set_defaults(fn=cmd_extract)

s = sub.add_parser('export', help='write one reference CSV per namespace')
s.add_argument('-l', '--locale', default='en')
s.add_argument('-n', '--namespace', help='comma-separated; default is all of them')
s.add_argument('-o', '--output', help='output directory')
s.set_defaults(fn=cmd_export)

s = sub.add_parser('build', help='build the mod pak from edits/*.csv')
s.add_argument('-i', '--input', nargs='*', help='specific CSVs (default: edits/*.csv)')
s.add_argument('--locales', default='en',
               help='comma-separated locales to patch (default: en)')
s.add_argument('--install', action='store_true', help='install after building')
s.add_argument('--force', action='store_true', help='silence the warning note')
s.set_defaults(fn=cmd_build)

s = sub.add_parser('install', help='copy the built pak into ~mods')
s.set_defaults(fn=cmd_install)

s = sub.add_parser('uninstall', help='remove the pak from ~mods')
s.set_defaults(fn=cmd_uninstall)

s = sub.add_parser('doctor', help='check prerequisites before doing anything')
s.set_defaults(fn=cmd_doctor)

s = sub.add_parser('status', help='show current state')
s.set_defaults(fn=cmd_status)

args = p.parse_args()
args.fn(args)
