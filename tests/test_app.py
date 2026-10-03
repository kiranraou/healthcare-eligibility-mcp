from streamlit.testing.v1 import AppTest


def _app():
    at = AppTest.from_file("../app.py", default_timeout=30)
    at.run()
    assert not at.exception
    return at


def test_app_loads_with_patients():
    at = _app()
    assert len(at.sidebar.selectbox[0].options) == 9
    assert [t.label for t in at.tabs] == ["Eligibility check", "Worklist", "Analytics", "AI assistant"]


def test_eligibility_check_shows_input_and_output():
    at = _app()
    at.sidebar.selectbox[0].select("P001")
    at.sidebar.button[0].click().run()
    assert not at.exception

    metrics = {m.label: m.value for m in at.metric}
    assert metrics["Status"] == "❌ Rejected"
    assert metrics["Denial risk"] == "🔴 High"
    codes = [c.value for c in at.code]
    assert codes[0].startswith("ISA*") and "ST*270" in codes[0]
    assert "ST*271" in codes[1]
    assert any("AAA 72" in e.value for e in at.error)


def test_worklist_and_analytics():
    at = _app()
    worklist_button = next(b for b in at.button if b.label == "Check all patients")
    worklist_button.click().run()
    assert not at.exception
    assert len(at.dataframe[-1].value) == 9

    pipeline_button = next(b for b in at.button if b.label.startswith("Run Bronze"))
    pipeline_button.click().run()
    assert not at.exception
    assert any("Silver +9 rows" in s.value for s in at.success)
    assert {m.label: m.value for m in at.metric}["Eligibility checks"] == "9"
