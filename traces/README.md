# Assignment 03 query traces

The Assignment 03 API emits one JSON line per query to stdout and persists the
same line in a per-pod file on the trace PVC. The records include raw queries,
model inputs and the retrieved context. The local demo uses public documents
and fictional or simulated queries.

```bash
bash scripts/ragops-k8s.sh export
python3.12 eval/trace_tools.py trace TRACE_ID
python3.12 eval/trace_tools.py prompt TRACE_ID
python3.12 eval/trace_tools.py summary --since 2026-11-21T00:00:00Z
```

For a semantic-cache hit the prompt command follows the originating trace.
Collect operational windows from the timestamp on each query record.
