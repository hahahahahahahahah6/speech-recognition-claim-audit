import ast
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from speech_recognition_claim_audit.audit import audit, evidence, phrases_in
from speech_recognition_claim_audit import phrases

EXPECTED_SPEECH = ('speech recognition', 'speech to text', 'automatic speech recognition', 'recognizes speech', 'recognises speech', 'transcribes speech', 'transcribe speech', 'speech transcription', 'converts speech to text', 'converts spoken words to text', 'converts spoken audio to text', 'voice to text', 'voice transcription', 'transcribes spoken audio', 'transcribe spoken audio', 'recognizes spoken words', 'recognises spoken words', 'speech recognizer', 'speech recognition engine', 'speech recognition support', 'supports speech recognition', 'speech recognition capability', 'transcribes voice', 'transcribe voice', 'voice dictation', 'real time speech recognition', 'real time transcription', 'live speech transcription', 'live transcription', 'transcribes audio to text', 'transcribe audio to text', 'audio to text transcription', 'audio transcription', 'spoken word recognition', 'spoken word transcription', 'recognizes what you say', 'recognises what you say', 'turns speech into text', 'turns spoken words into text', 'speech to text conversion', 'speech to text transcription', 'asr engine', 'supports asr', 'asr support', 'automatic transcription', 'transcribes what you say')
EXPECTED_NOSPEECH = ('no speech recognition', 'does not recognize speech', 'does not recognise speech', 'without speech recognition', 'no speech to text', 'does not transcribe speech', 'without speech to text', 'no automatic speech recognition', 'does not support speech recognition', 'speech recognition not supported', 'no voice to text', 'does not transcribe spoken audio', 'without automatic speech recognition', 'cannot recognize speech', 'cannot transcribe speech', 'never transcribes speech', 'no speech transcription', 'does not convert speech to text', 'no audio transcription', 'does not transcribe audio to text', 'without speech transcription', 'no live transcription', 'does not support asr', 'no asr support', 'cannot recognize spoken words', 'does not recognize spoken words')


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def put(self, path, text):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)

    def cli(self, *args):
        return subprocess.run([sys.executable, '-m', 'speech_recognition_claim_audit', str(self.root), *args], capture_output=True, text=True)

    def test_phrase_oracle(self):
        tree = ast.parse(Path(phrases.__file__).read_text())
        parsed = {node.targets[0].id: ast.literal_eval(node.value) for node in tree.body if isinstance(node, ast.Assign)}
        self.assertEqual(parsed['SPEECH'], EXPECTED_SPEECH)
        self.assertEqual(parsed['NOSPEECH'], EXPECTED_NOSPEECH)
        self.assertEqual(len(parsed['SPEECH']), 46)
        self.assertEqual(len(parsed['NOSPEECH']), 26)
        for phrase in EXPECTED_SPEECH:
            self.assertEqual(phrases_in(phrase), {'SPEECH'})
        for phrase in EXPECTED_NOSPEECH:
            self.assertEqual(phrases_in(phrase), {'NOSPEECH'})

    def test_good(self):
        self.put('README.md', 'speech recognition')
        self.put('app.py', 'model.transcribe(audio)')
        self.assertEqual(self.cli().returncode, 0)
        self.assertEqual(audit(self.root)['verdict'], 'PASS')

    def test_bad_exact(self):
        self.put('README.md', 'speech recognition')
        result = self.cli('--json')
        self.assertEqual(result.returncode, 2)
        report = json.loads(result.stdout)
        self.assertEqual(report['verdict'], 'FAIL')
        self.assertEqual(report['findings'][0]['id'], 'documented-speech-recognition-claim-false')
        self.assertEqual(report['findings'][0]['severity'], 'HIGH')
        self.assertTrue(report['findings'][0]['message'])

    def test_test_only(self):
        self.put('README.md', 'speech recognition')
        self.put('tests/helper.py', 'model.transcribe(audio)')
        self.assertEqual(audit(self.root)['findings'][0]['severity'], 'MEDIUM')
        self.assertEqual(self.cli().returncode, 0)
        self.assertEqual(self.cli('--fail-on', 'MEDIUM').returncode, 2)

    def test_denial(self):
        self.put('README.md', 'does not recognize speech')
        self.put('app.py', 'model.transcribe(audio)')
        self.assertEqual(self.cli().returncode, 2)
        self.assertEqual(audit(self.root)['findings'][0]['id'], 'documented-no-speech-recognition-claim-false')
        self.assertEqual(audit(self.root)['findings'][0]['severity'], 'HIGH')

    def test_no_claim(self):
        self.put('README.md', 'An application.')
        self.assertEqual(audit(self.root)['verdict'], 'PASS')

    def test_no_source_asymmetry(self):
        self.put('README.md', 'speech recognition')
        self.assertEqual(audit(self.root)['findings'][0]['severity'], 'HIGH')
        self.put('README.md', 'no speech recognition')
        self.assertEqual(audit(self.root)['verdict'], 'PASS')

    def test_fences_inline_normalization(self):
        for marker in ('```', '~~~'):
            for spaces in range(4):
                self.assertEqual(phrases_in(' ' * spaces + marker + '\nspeech recognition\n' + ' ' * spaces + marker), set())
        self.assertEqual(phrases_in('`speech recognition`'), {'SPEECH'})
        self.assertEqual(phrases_in("doesn't recognize speech"), {'NOSPEECH'})
        self.assertEqual(phrases_in('SPEECH-TO-TEXT'), {'SPEECH'})
        self.assertEqual(phrases_in('speech\nrecognition'), {'SPEECH'})
        self.assertEqual(phrases_in('speech\n\nrecognition'), set())
        self.assertEqual(phrases_in('xspeech recognitionx'), set())

    def test_docs_selection(self):
        self.put('nested/docs/a.md', 'speech recognition')
        self.assertTrue(audit(self.root)['findings'])
        self.assertEqual(audit(self.root, ['nested'])['verdict'], 'PASS')
        self.put('nested/docs/a.md', 'neutral')
        self.put('nested/README.md', 'speech recognition')
        self.assertEqual(audit(self.root)['verdict'], 'PASS')

    def test_exclusions(self):
        self.put('README.md', 'speech recognition')
        for path in ('fixtures/a.py', 'docs/a.py', 'examples/a.py'):
            self.put(path, 'm.transcribe(a)')
        self.assertEqual(audit(self.root)['findings'][0]['severity'], 'HIGH')
        for path in ('test_one.py', 'one_test.py'):
            self.put(path, 'm.transcribe(a)')
        self.assertEqual(audit(self.root)['findings'][0]['severity'], 'MEDIUM')

    def test_symlinks(self):
        self.put('README.md', 'speech recognition')
        with tempfile.TemporaryDirectory() as elsewhere:
            target = Path(elsewhere)
            (target / 'app.py').write_text('m.transcribe(a)')
            (self.root / 'link.py').symlink_to(target / 'app.py')
            (self.root / 'linked').symlink_to(target, target_is_directory=True)
            self.assertEqual(audit(self.root)['findings'][0]['severity'], 'HIGH')

    def test_split_pairs(self):
        self.put('README.md', 'speech recognition')
        for constructor, method, prefix in [('KaldiRecognizer', 'AcceptWaveform', ''), ('Model', 'stt', 'import deepspeech\n'), ('SpeechRecognizer', 'recognize_once', ''), ('SpeechClient', 'recognize', ''), ('Recognizer', 'recognize_google', 'import speech_recognition\n')]:
            self.put('one.py', prefix + 'r = ' + constructor + '()')
            self.put('two.py', prefix + 'r.' + method + '(a)')
            self.assertEqual(audit(self.root)['findings'][0]['severity'], 'HIGH')

    def test_usage_version(self):
        self.assertEqual(self.cli('--version').stdout.strip(), '0.1.0')
        self.assertEqual(self.cli('--unknown').returncode, 1)
        self.assertEqual(self.cli('--fail-on', 'invalid').returncode, 1)
        result = subprocess.run([sys.executable, '-m', 'speech_recognition_claim_audit'], capture_output=True)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(subprocess.run([sys.executable, '-m', 'speech_recognition_claim_audit', str(self.root / 'missing')], capture_output=True).returncode, 1)

    def test_console_entry_definition(self):
        from importlib.metadata import distribution
        try:
            entries = distribution('speech-recognition-claim-audit').entry_points
        except Exception:
            project = Path(__file__).resolve().parents[1] / 'pyproject.toml'
            self.assertIn('speech-recognition-claim-audit = "speech_recognition_claim_audit.audit:main"', project.read_text())
        else:
            self.assertTrue(any(e.name == 'speech-recognition-claim-audit' and e.value == 'speech_recognition_claim_audit.audit:main' for e in entries))

def route_test(source, expected):
    def test(self):
        self.assertEqual(evidence(source), expected, source)
    return test

setattr(AuditTests, 'test_positive_0', route_test('m.transcribe(a)', True))
setattr(AuditTests, 'test_positive_1', route_test('import speech_recognition as sr\nr = sr.Recognizer()\nr.recognize_google(a)', True))
setattr(AuditTests, 'test_positive_2', route_test('from speech_recognition import Recognizer as R\nr = R()\nr.recognize_whisper(a)', True))
setattr(AuditTests, 'test_positive_3', route_test('from speech_recognition import recognize_sphinx as f\nf(a)', True))
setattr(AuditTests, 'test_positive_4', route_test('from vosk import KaldiRecognizer as K\nr = K()\nr.AcceptWaveform(a)', True))
setattr(AuditTests, 'test_positive_5', route_test('import deepspeech as d\nr = d.Model()\nr.stt(a)', True))
setattr(AuditTests, 'test_positive_6', route_test('pipeline("automatic-speech-recognition")', True))
setattr(AuditTests, 'test_positive_7', route_test('t.pipeline("asr")', True))
setattr(AuditTests, 'test_positive_8', route_test('c.audio.transcriptions.create(file=a)', True))
setattr(AuditTests, 'test_positive_9', route_test('r = sdk.SpeechRecognizer()\nr.recognize_once()', True))
setattr(AuditTests, 'test_positive_10', route_test('r = SpeechRecognizer()\nr.recognize_once_async()', True))
setattr(AuditTests, 'test_positive_11', route_test('r = speech.SpeechClient()\nr.recognize(a)', True))
setattr(AuditTests, 'test_positive_12', route_test('r = SpeechClient()\nr.streaming_recognize(a)', True))
setattr(AuditTests, 'test_positive_13', route_test('from arbitrary import Model as M\nimport deepspeech\nr = M()\nr.stt(a)', True))
setattr(AuditTests, 'test_positive_14', route_test('def pipeline(x): pass\npipeline("asr")', True))
setattr(AuditTests, 'test_positive_15', route_test('def create(): pass\nc.transcriptions.create()', True))
setattr(AuditTests, 'test_positive_16', route_test('obj.speech_to_text(a)', True))
setattr(AuditTests, 'test_positive_17', route_test('from helper import speech_to_text as f\nf(a)', True))
setattr(AuditTests, 'test_positive_18', route_test('obj.recognize_speech(a)', True))
setattr(AuditTests, 'test_positive_19', route_test('from helper import recognize_speech as f\nf(a)', True))
setattr(AuditTests, 'test_positive_20', route_test('obj.transcribe_audio(a)', True))
setattr(AuditTests, 'test_positive_21', route_test('from helper import transcribe_audio as f\nf(a)', True))
setattr(AuditTests, 'test_positive_22', route_test('obj.transcribe_speech(a)', True))
setattr(AuditTests, 'test_positive_23', route_test('from helper import transcribe_speech as f\nf(a)', True))
setattr(AuditTests, 'test_positive_24', route_test('obj.convert_speech_to_text(a)', True))
setattr(AuditTests, 'test_positive_25', route_test('from helper import convert_speech_to_text as f\nf(a)', True))
setattr(AuditTests, 'test_positive_26', route_test('import speech_recognition\nr=Recognizer()\nr.recognize_google(a)', True))
setattr(AuditTests, 'test_positive_27', route_test('import speech_recognition\nr=Recognizer()\nr.recognize_whisper(a)', True))
setattr(AuditTests, 'test_positive_28', route_test('import speech_recognition\nr=Recognizer()\nr.recognize_sphinx(a)', True))
setattr(AuditTests, 'test_positive_29', route_test('import speech_recognition\nr=Recognizer()\nr.recognize_bing(a)', True))
setattr(AuditTests, 'test_positive_30', route_test('import speech_recognition\nr=Recognizer()\nr.recognize_azure(a)', True))
setattr(AuditTests, 'test_positive_31', route_test('import speech_recognition\nr=Recognizer()\nr.recognize_houndify(a)', True))
setattr(AuditTests, 'test_positive_32', route_test('import speech_recognition\nr=Recognizer()\nr.recognize_ibm(a)', True))
setattr(AuditTests, 'test_positive_33', route_test('import speech_recognition\nr=Recognizer()\nr.recognize_wit(a)', True))
setattr(AuditTests, 'test_positive_34', route_test('import speech_recognition\nr=Recognizer()\nr.recognize_vosk(a)', True))
setattr(AuditTests, 'test_negative_0', route_test('import whisper', False))
setattr(AuditTests, 'test_negative_1', route_test('vad.is_speech(a)', False))
setattr(AuditTests, 'test_negative_2', route_test('get_speech_timestamps(a)', False))
setattr(AuditTests, 'test_negative_3', route_test('pvporcupine.create()', False))
setattr(AuditTests, 'test_negative_4', route_test('openwakeword.Model().predict(a)', False))
setattr(AuditTests, 'test_negative_5', route_test('verify_speaker(a)', False))
setattr(AuditTests, 'test_negative_6', route_test('model.verify_files(a,b)', False))
setattr(AuditTests, 'test_negative_7', route_test('pipeline("speaker-verification")', False))
setattr(AuditTests, 'test_negative_8', route_test('p.diarize(a)', False))
setattr(AuditTests, 'test_negative_9', route_test('pipeline("diarization")', False))
setattr(AuditTests, 'test_negative_10', route_test('nr.reduce_noise(a)', False))
setattr(AuditTests, 'test_negative_11', route_test('suppress_noise(a)', False))
setattr(AuditTests, 'test_negative_12', route_test('model.detect_language(a)', False))
setattr(AuditTests, 'test_negative_13', route_test('detect(a)', False))
setattr(AuditTests, 'test_negative_14', route_test('pipeline("language-identification")', False))
setattr(AuditTests, 'test_negative_15', route_test('t.translate(a)', False))
setattr(AuditTests, 'test_negative_16', route_test('engine.say(a)', False))
setattr(AuditTests, 'test_negative_17', route_test('gTTS(a).save(b)', False))
setattr(AuditTests, 'test_negative_18', route_test('import speech_recognition as sr\nr=sr.Recognizer()\nr.listen(a)', False))
setattr(AuditTests, 'test_negative_19', route_test('sd.rec(a)', False))
setattr(AuditTests, 'test_negative_20', route_test('stream.read()', False))
setattr(AuditTests, 'test_negative_21', route_test('play(a)', False))
setattr(AuditTests, 'test_negative_22', route_test('librosa.load(a)', False))
setattr(AuditTests, 'test_negative_23', route_test('soundfile.read(a)', False))
setattr(AuditTests, 'test_negative_24', route_test('wave.read(a)', False))
setattr(AuditTests, 'test_negative_25', route_test('torchaudio.load(a)', False))
setattr(AuditTests, 'test_negative_26', route_test('r=KaldiRecognizer()', False))
setattr(AuditTests, 'test_negative_27', route_test('import deepspeech\nr=Model()', False))
setattr(AuditTests, 'test_negative_28', route_test('r=SpeechRecognizer()', False))
setattr(AuditTests, 'test_negative_29', route_test('r=SpeechClient()', False))
setattr(AuditTests, 'test_negative_30', route_test('r.AcceptWaveform(a)', False))
setattr(AuditTests, 'test_negative_31', route_test('import deepspeech\nr.stt(a)', False))
setattr(AuditTests, 'test_negative_32', route_test('r.recognize_once()', False))
setattr(AuditTests, 'test_negative_33', route_test('r.recognize(a)', False))
setattr(AuditTests, 'test_negative_34', route_test('r.recognize_google(a)', False))
setattr(AuditTests, 'test_negative_35', route_test('subprocess.run(["whisper", "a.wav"])', False))
setattr(AuditTests, 'test_negative_36', route_test('x="whisper transcribe"', False))
setattr(AuditTests, 'test_negative_37', route_test('obj.transcribe', False))
setattr(AuditTests, 'test_negative_38', route_test('broken syntax !!!', False))
setattr(AuditTests, 'test_negative_39', route_test('speech_to_text(a)', False))
setattr(AuditTests, 'test_negative_40', route_test('recognize_google(a)', False))
setattr(AuditTests, 'test_negative_41', route_test('def transcribe(a): pass\nm.transcribe(a)', False))
setattr(AuditTests, 'test_negative_42', route_test('class C:\n def recognize_google(self,a): pass\nimport speech_recognition\nr=Recognizer()\nr.recognize_google(a)', False))
setattr(AuditTests, 'test_negative_43', route_test('def speech_to_text(a): pass\nm.speech_to_text(a)', False))

setattr(AuditTests, 'test_shadowed_import_alias', route_test('from helper import speech_to_text as f\ndef f(a): pass\nf(a)', False))
setattr(AuditTests, 'test_shadowed_constructor_alias', route_test('from speech_recognition import Recognizer as R\ndef R(): pass\nr=R()\nr.recognize_google(a)', False))

if __name__ == '__main__':
    unittest.main()
