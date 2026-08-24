from capitalguard.domain.ownership import (
    OWNERSHIP_CONTRACT,
    Responsibility,
    TruthLayer,
    ownership_for,
    validate_ownership_contract,
)


def test_g7_ownership_contract_is_unique_and_complete():
    validate_ownership_contract()
    assert len(OWNERSHIP_CONTRACT) == len(set(entry.responsibility for entry in OWNERSHIP_CONTRACT))
    assert all(entry.owner for entry in OWNERSHIP_CONTRACT)
    assert all(entry.write_scope for entry in OWNERSHIP_CONTRACT)


def test_core_truth_owners_are_explicit():
    assert ownership_for(Responsibility.SOURCE_RECEIPT).truth_layer == TruthLayer.SOURCE_TRUTH
    assert ownership_for(Responsibility.SEMANTIC_MATERIALIZATION).truth_layer == TruthLayer.SEMANTIC_TRUTH
    assert ownership_for(Responsibility.REPLAY_MARKET_EVIDENCE).truth_layer == TruthLayer.MARKET_FACT
    assert ownership_for(Responsibility.LIVE_EXECUTION).truth_layer == TruthLayer.EXECUTION_STATE


def test_ownership_contract_does_not_assign_read_models_as_writers_of_source_truth():
    source_owner = ownership_for(Responsibility.SOURCE_RECEIPT)
    performance_owner = ownership_for(Responsibility.PERFORMANCE_READ_MODEL)
    trust_owner = ownership_for(Responsibility.HISTORICAL_TRUST_RELEASE)

    assert "read model" not in source_owner.owner.lower()
    assert "none to source" in performance_owner.side_effect_scope.lower()
    assert "cannot enable public ranking" in trust_owner.side_effect_scope.lower()
