# Demo runbook

The Bash driver presents the deterministic pipeline in six screens. It uses
the committed synthetic data and needs no API key or network connection.

## Prepare

Complete the environment setup in the [README](../README.md), then run from
the repository root in Bash (Git Bash on Windows):

```bash
./tools/pitch.sh --check
./tools/pitch.sh
```

The preflight checks the environment and prepares the exception CSV and report.
Press Enter to advance each screen. For unattended recording:

```bash
./tools/pitch.sh --auto
```

From Windows PowerShell:

```powershell
& "C:\Program Files\Git\bin\bash.exe" tools/pitch.sh --check
& "C:\Program Files\Git\bin\bash.exe" tools/pitch.sh --auto
```

Open `runs/demo/index.html` in a browser before presenting. Use the
[video script](VIDEO_SCRIPT.md) for the spoken explanation.

## Screens

| Screen | Show | Explain |
|---|---|---|
| 1 | Primary baselines | Exact joins and amount lookup provide the comparison |
| 2 | Agent report | 94/100 correct cases, no false matches; B2 has 16 incorrect allocations |
| 3 | Abstention and queue summary | Unattributed refunds and accounting discrepancies are separate amounts |
| 3b | One exception row | Source evidence, category, and recommended action |
| 4 | Holdout rerun | Five additional seeds; the fixed scenario mix explains the repeated 94/100 |
| 5 | HTML report | A portable report that can be opened without a server |

The holdout screen reruns the recorded seeds. It is not a fresh held-out
evaluation. Live-model results are a separate recorded experiment in
[LIVE_MODEL.md](LIVE_MODEL.md), not part of the offline demo.

## Troubleshooting

- If imports fail, check that the project environment has the requirements
  installed. The driver sets `PYTHONPATH=src` itself.
- If datasets are missing, run `make data` before preflight.
- If the report differs from the script, use the actual output and investigate
  the change before recording.
- Use the `--check` mode to prepare output files before opening the dashboard.
