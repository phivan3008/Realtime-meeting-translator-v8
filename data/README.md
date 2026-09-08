# `data/` — local-only working data

This whole directory is excluded from Git by `.gitignore`, except this README.

Nothing in here is ever committed. It holds confidential meeting material and
large binaries that `requirements.md` Section 25.14 forbids putting in the
repository.

## Layout

```text
data/
  recordings/   real meeting recordings (source of every category A fixture)
  clips/        clips cut from a recording, created by tools/, with provenance
  captures/     real WebSocket traffic captured from an actual server run
  output/       local benchmark and test output
```

## Placing the real meeting recording

Copy the real meeting recording to `data/recordings/` on this machine and then
record its provenance:

```bash
python tools/hash_file.py data/recordings/<filename>
```

The resulting SHA-256, duration, sample rate and channel count belong in
`tests/manifests/`, which *is* committed. The audio itself never is.
