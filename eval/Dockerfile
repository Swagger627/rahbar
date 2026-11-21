FROM rahbar-api:a3
WORKDIR /suite
COPY eval/ragops_eval.py eval/release_gate.py eval/result_io.py eval/trace_tools.py eval/coverage_check.py ./
COPY eval/golden.jsonl eval/golden_expanded.jsonl eval/embedding_pairs.jsonl eval/live_queries.jsonl ./
ENTRYPOINT ["python", "/suite/ragops_eval.py"]
CMD []
