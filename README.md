# Documentation audit

An offline static audit tool for comparing documentation claims with Python call evidence. It reads the root readme and Markdown under documentation directories, excludes fenced examples, and parses source without executing it.

Install and run:

```sh
python3 -m pip install speech-recognition-claim-audit
speech-recognition-claim-audit . --json
python3 -m speech_recognition_claim_audit . --verbose
```

The report contains a verdict, findings with messages and severity, and evidence paths. These identifiers describe conflicting claims:

```text
documented-speech-recognition-claim-false
documented-no-speech-recognition-claim-false
```

Production evidence supports positive claims and contradicts denials. Evidence found only in tests produces a medium finding for a positive claim. Static matching cannot prove runtime correctness.

The default failure threshold is HIGH. Select MEDIUM to fail on test evidence alone. Repeat the ignore option to exclude relative path prefixes. Exit status is zero below the threshold, two for findings at the threshold, and one for usage or input errors.

```sh
speech-recognition-claim-audit . --fail-on MEDIUM --ignore build --ignore dist
python3 -m unittest discover
python3 -m build
```
