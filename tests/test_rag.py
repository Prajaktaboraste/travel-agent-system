from app.rag import Retriever, tokenize


def test_tokenize_drops_stopwords_and_plurals():
    assert tokenize("The beaches of Goa are the best") == ["beache", "goa", "best"]
    assert tokenize("Hotels, hotel!") == ["hotel", "hotel"]          # plural/singular index identically
    assert tokenize("") == []


def test_ranks_relevant_city_first(seeded):
    hits = seeded.retriever.search("best time to visit Goa")
    assert hits and hits[0]["city"] == "Goa" and "November" in hits[0]["text"]


def test_city_filter_excludes_other_cities(seeded):
    hits = seeded.retriever.search("best time to visit", city="Tokyo", top_k=10)
    assert hits and all(h["city"] in ("Tokyo", None) for h in hits)


def test_general_documents_found(seeded):
    hits = seeded.retriever.search("schengen visa passport validity")
    assert hits and hits[0]["title"] == "Visa and Travel Documents"


def test_no_match_returns_empty(seeded):
    assert seeded.retriever.search("quantum chromodynamics lagrangian") == []
    assert seeded.retriever.search("the of and") == []


def test_top_k_and_scores_sorted(seeded):
    hits = seeded.retriever.search("budget per day", top_k=3)
    assert len(hits) <= 3 and [h["score"] for h in hits] == sorted((h["score"] for h in hits), reverse=True)


def test_empty_knowledge_base(container):
    assert container.retriever.search("anything") == []


def test_reindexes_when_documents_change(container):
    r = container.retriever
    assert r.search("zanzibar spice tour") == []
    container.kb.add("Zanzibar", "Zanzibar is famous for its spice tour and Stone Town.", "Zanzibar")
    assert r.search("zanzibar spice tour")[0]["title"] == "Zanzibar"
    container.kb.delete(1)
    assert r.search("zanzibar spice tour") == []


def test_load_directory_is_idempotent(container):
    from app.config import BASE_DIR
    d = str(BASE_DIR / "data" / "knowledge")
    assert container.kb.load_directory(d) == 8
    assert container.kb.load_directory(d) == 0


def test_single_document_corpus_still_matches(container):
    """Regression: an absolute score cut-off used to hide matches in tiny knowledge bases."""
    container.kb.add("Rome", "Rome has the Colosseum and the Vatican museums.", "Rome")
    hits = container.retriever.search("Colosseum", city="Rome")
    assert len(hits) == 1 and hits[0]["title"] == "Rome"


def test_partial_query_coverage_required(seeded):
    # 'budget' alone is relevant, but one matching word in a long off-topic question is not enough
    assert seeded.retriever.search("budget")
    assert seeded.retriever.search("quantum lagrangian chromodynamics budget") == []
