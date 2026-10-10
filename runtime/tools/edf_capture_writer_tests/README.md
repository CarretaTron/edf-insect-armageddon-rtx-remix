# Compatibility capture writer tests

This harness builds only the C++17 writer and the repository's existing SHA1 implementation. It does not require Vulkan, RTX SDKs, or the Bridge.

From a Visual Studio x64 developer PowerShell:

```powershell
./tools/edf_capture_writer_tests/run.ps1
```

For another compiler, pass `-Compiler 'C:\path\to\clang++.exe'`. The script preserves build files and captured test artifacts under `_build/edf_capture_writer_tests` by default.

The tests verify reproducible hashes, JSON escaping, copied immutable payloads, content deduplication, empty blobs, bounded queues, total byte limits, terminal truncation records, queued-record preservation, concurrent producers, repeated `finish()`, refusing existing sessions, and isolated filesystem/input failures.

The output contains `records.jsonl` and `blobs/<sha1>.bin`. `BlobRef.available` reports acceptance into the asynchronous queue, not a completed filesystem write. Always check `hasError()` after `finish()`, and require the terminal `capture_status` record when reading a completed session. A missing terminal record indicates an interrupted or incomplete capture.

The writer's third constructor argument controls queued payload memory, including the in-flight write. It defaults to 16 MiB. `capture_status.max_queue_bytes` records that budget and `peak_queue_bytes` records the maximum charged item memory. The disk budget remains independent. The standalone tests verify that a 20 MiB blob is rejected by the default queue, accepted and drained by a 64 MiB queue, deduplicated by hash, and still rejected when it exceeds a smaller disk budget. Invalid tiny queues fail before creating a manifest.

The byte limit includes the JSONL and blob payloads, with 1 KiB reserved for the final status. Budgets below 1 KiB are rejected. The default 16 MiB queue limit includes the payload currently being written. Either limit stops new payloads immediately while preserving accepted items.

Blob identity is SHA1 over the exact bytes, independent of process addresses. Same-hash, different-size collisions fail the writer. Same-hash, same-size payloads are deduplicated using the repository's SHA1 identity without rereading files on the rendering thread. This diagnostic identity is not intended for adversarial inputs.
