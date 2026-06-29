from src import run_customer_pipeline


def test_run_all_pipelines_truncates_audit_before_profile_batch(monkeypatch) -> None:
    calls = []

    monkeypatch.setattr(
        run_customer_pipeline,
        "load_pipeline_config",
        lambda: {"One": {"source_table": "raw.One"}},
    )
    monkeypatch.setattr(
        run_customer_pipeline,
        "truncate_audit_tables",
        lambda: calls.append("truncate"),
    )
    monkeypatch.setattr(
        run_customer_pipeline,
        "_run_pipeline_batch",
        lambda **_: calls.append("batch"),
    )

    run_customer_pipeline.run_all_pipelines(profile_only=True)

    assert calls == ["truncate", "batch"]


def test_run_all_pipelines_does_not_truncate_audit_before_non_profile_batch(monkeypatch) -> None:
    calls = []

    monkeypatch.setattr(
        run_customer_pipeline,
        "load_pipeline_config",
        lambda: {"One": {"source_table": "raw.One"}},
    )
    monkeypatch.setattr(
        run_customer_pipeline,
        "truncate_audit_tables",
        lambda: calls.append("truncate"),
    )
    monkeypatch.setattr(
        run_customer_pipeline,
        "_run_pipeline_batch",
        lambda **_: calls.append("batch"),
    )

    run_customer_pipeline.run_all_pipelines(raw_only=True)

    assert calls == ["batch"]
