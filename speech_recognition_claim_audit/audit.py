"""Offline documentation and Python AST audit."""
import argparse
import ast
import json
import os
from pathlib import Path
import re
import sys

from .phrases import SPEECH, NOSPEECH

RECOGNIZE = {'recognize_' + x for x in ('google', 'whisper', 'sphinx', 'bing', 'azure', 'houndify', 'ibm', 'wit', 'vosk')}
GENERIC = {'speech_to_text', 'recognize_speech', 'transcribe_audio', 'transcribe_speech', 'convert_speech_to_text'}


def normalize(text):
    return ' '.join(text.lower().replace("n't", ' not').replace('-', ' ').split())


def phrases_in(text):
    lines = []
    fence = None
    for line in text.splitlines():
        match = re.match(r'^ {0,3}(`{3,}|~{3,})(.*)$', line)
        if fence:
            if match and match[1][0] == fence[0] and len(match[1]) >= len(fence) and not match[2].strip():
                fence = None
            continue
        if match:
            fence = match[1]
            lines.append('')
        else:
            lines.append(line)
    found = set()
    for paragraph in re.split(r'\n\s*\n', '\n'.join(lines)):
        value = normalize(paragraph)
        for family, phrases in [('NOSPEECH', NOSPEECH), ('SPEECH', SPEECH)]:
            for phrase in sorted(phrases, key=lambda p: (-len(p), p)):
                pattern = r'\b' + re.escape(phrase) + r'\b'
                if re.search(pattern, value):
                    found.add(family)
                    value = re.sub(pattern, ' ', value)
    return found


def evidence(source):
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return False
    nodes = list(ast.walk(tree))
    aliases = {}
    imports = set()
    for node in nodes:
        if isinstance(node, ast.Import):
            for item in node.names:
                imports.add(item.name.split('.')[0])
                aliases[item.asname or item.name.split('.')[0]] = item.name
        elif isinstance(node, ast.ImportFrom):
            imports.add((node.module or '').split('.')[0])
            for item in node.names:
                aliases[item.asname or item.name] = (node.module or '') + '.' + item.name
    def name(node):
        if isinstance(node, ast.Name):
            return aliases.get(node.id, node.id).split('.')[-1]
        if isinstance(node, ast.Attribute):
            return node.attr
        return ''
    shadows = {n.name for n in nodes if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    bindings = {}
    for node in nodes:
        if isinstance(node, (ast.Assign, ast.AnnAssign)) and isinstance(node.value, ast.Call):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name):
                    constructor = node.value.func
                    if not (isinstance(constructor, ast.Name) and constructor.id in shadows):
                        bindings.setdefault(target.id, set()).add(name(constructor))
    for node in nodes:
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        attr = name(func)
        if attr == 'pipeline' and node.args and isinstance(node.args[0], ast.Constant) and node.args[0].value in ('automatic-speech-recognition', 'asr'):
            return True
        if isinstance(func, ast.Attribute) and func.attr == 'create':
            base = func.value
            while isinstance(base, ast.Attribute):
                if base.attr == 'transcriptions':
                    return True
                base = base.value
        if attr in shadows or (isinstance(func, ast.Name) and func.id in shadows):
            continue
        if isinstance(func, ast.Attribute):
            if attr == 'transcribe' or attr in GENERIC:
                return True
            bound = bindings.get(func.value.id, set()) if isinstance(func.value, ast.Name) else set()
            if attr in RECOGNIZE and 'Recognizer' in bound and 'speech_recognition' in imports and 'Recognizer' not in shadows:
                return True
            for methods, constructor, required in [({'AcceptWaveform'}, 'KaldiRecognizer', None), ({'stt'}, 'Model', 'deepspeech'), ({'recognize_once', 'recognize_once_async'}, 'SpeechRecognizer', None), ({'recognize', 'streaming_recognize'}, 'SpeechClient', None)]:
                if attr in methods and constructor in bound and constructor not in shadows and (required is None or required in imports):
                    return True
        elif isinstance(func, ast.Name) and func.id in aliases and attr in RECOGNIZE | GENERIC:
            return True
    return False


def audit(root, ignore=()):
    root = Path(root)
    if not root.is_dir():
        raise OSError('root must be a readable directory')
    claims = set()
    production = []
    tests = []
    def walk_error(error):
        raise error
    for directory, dirs, files in os.walk(root, onerror=walk_error):
        relative = Path(directory).relative_to(root)
        def excluded(path):
            rel = path.relative_to(root).as_posix()
            return path.is_symlink() or any(rel == p.rstrip('/') or rel.startswith(p.rstrip('/') + '/') for p in ignore)
        dirs[:] = sorted(d for d in dirs if not excluded(Path(directory) / d))
        for filename in sorted(files):
            path = Path(directory) / filename
            if excluded(path):
                continue
            parts = path.relative_to(root).parts
            if (parts == ('README.md',) or ('docs' in parts[:-1] and path.suffix == '.md')):
                claims.update(phrases_in(path.read_text(encoding='utf-8')))
            if path.suffix != '.py' or any(p in parts[:-1] for p in ('fixtures', 'docs', 'examples')):
                continue
            is_test = 'tests' in parts[:-1] or filename.startswith('test_') or filename.endswith('_test.py')
            if evidence(path.read_text(encoding='utf-8')):
                (tests if is_test else production).append(path.relative_to(root).as_posix())
    findings = []
    if 'SPEECH' in claims and not production:
        severity = 'MEDIUM' if tests else 'HIGH'
        findings.append({'id': 'documented-speech-recognition-claim-false', 'severity': severity, 'message': 'Documentation claim has evidence only in tests.' if tests else 'Documentation claim has no production evidence.'})
    if 'NOSPEECH' in claims and production:
        findings.append({'id': 'documented-no-speech-recognition-claim-false', 'severity': 'HIGH', 'message': 'Documentation denial conflicts with production evidence.'})
    return {'verdict': 'FAIL' if findings else 'PASS', 'findings': findings, 'production_evidence': production, 'test_evidence': tests}


class Parser(argparse.ArgumentParser):
    def error(self, message):
        self.print_usage(sys.stderr)
        self.exit(1, '%s: error: %s\n' % (self.prog, message))


def main(argv=None):
    parser = Parser(description=__doc__)
    parser.add_argument('root')
    parser.add_argument('--version', action='version', version='0.1.0')
    parser.add_argument('--json', action='store_true')
    parser.add_argument('--verbose', action='store_true')
    parser.add_argument('--ignore', action='append', default=[])
    parser.add_argument('--fail-on', choices=['LOW', 'MEDIUM', 'HIGH'], default='HIGH')
    args = parser.parse_args(argv)
    try:
        result = audit(args.root, args.ignore)
    except (OSError, UnicodeError) as error:
        print(str(error), file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(result, sort_keys=True))
    else:
        print(result['verdict'])
        for finding in result['findings']:
            print('{id} {severity}: {message}'.format(**finding))
        if args.verbose:
            print('Production evidence: ' + ', '.join(result['production_evidence']))
            print('Test evidence: ' + ', '.join(result['test_evidence']))
    levels = {'LOW': 0, 'MEDIUM': 1, 'HIGH': 2}
    return 2 if any(levels[f['severity']] >= levels[args.fail_on] for f in result['findings']) else 0
