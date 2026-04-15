from catalysts import edgar
from catalysts.types import RawCatalyst

SAMPLE_ATOM = b"""<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <title>8-K - ACME CORP (0001234567) (Filer)</title>
    <link href="https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&amp;CIK=0001234567&amp;type=8-K"/>
    <updated>2026-04-13T10:00:00-04:00</updated>
    <id>urn:tag:sec.gov,2008:accession-number=0001234567-26-000001</id>
    <category term="8-K"/>
  </entry>
</feed>
"""

def test_parse_atom_entries():
    entries = edgar._parse_atom(SAMPLE_ATOM, ticker="ACME")
    assert len(entries) == 1
    e = entries[0]
    assert isinstance(e, RawCatalyst)
    assert e.ticker == "ACME"
    assert e.source == "edgar"
    assert e.form_type == "8-K"
    assert "0001234567-26-000001" in e.source_id
    assert e.url.startswith("https://www.sec.gov/")

def test_fetch_stubbed(monkeypatch):
    captured = {}
    def fake_get(url, headers, timeout):
        captured["ua"] = headers.get("User-Agent", "")
        class R:
            status_code = 200
            content = SAMPLE_ATOM
            def raise_for_status(self): pass
        return R()
    monkeypatch.setattr(edgar.requests, "get", fake_get)
    items = edgar.fetch(["ACME"], since_hours=999999)
    assert len(items) == 1
    assert captured["ua"].startswith("Dealscout")
