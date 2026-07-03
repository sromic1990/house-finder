"""Tests for the Finnish-text -> feature parsers. Run: python -m pytest -q
(or: python tests/test_normalize.py for a dependency-free check)."""
from collector.normalize import (parse_balcony, parse_duplex, parse_land_ownership,
                                  parse_parking, parse_property_type, parse_sauna,
                                  parse_toilets)


def test_sauna_private():
    assert parse_sauna("Tilava 3h+k+s, oma sauna") == {
        "present": True, "private": True, "shared": False}


def test_sauna_shared_only():
    r = parse_sauna("Asunnossa ei saunaa, vain taloyhtiön sauna")
    assert r["present"] and not r["private"] and r["shared"]


def test_sauna_absent():
    assert parse_sauna("Valoisa 2h+kk parvekkeella") is None


def test_balcony_glazed():
    assert parse_balcony("Lasitettu parveke etelään") == {
        "present": True, "glazed": True}


def test_balcony_unglazed():
    assert parse_balcony("Avoin parveke") == {"present": True, "glazed": False}


def test_balcony_unknown_glazing():
    assert parse_balcony("Parveke") == {"present": True, "glazed": None}


def test_balcony_absent():
    assert parse_balcony("3h + k + s") is None


def test_parking_ranking():
    assert parse_parking("Autotalli")["type"] == "garage"
    assert parse_parking("Autohalli")["type"] == "hall"
    assert parse_parking("Autokatos")["type"] == "covered"
    assert parse_parking("Autopaikka lämpötolpalla")["type"] == "open_pole"
    assert parse_parking("Autopaikka pihalla")["type"] == "open"
    assert parse_parking("Ei tietoja") is None


def test_room_code_abbreviations():
    # real Finnish room codes from Oikotie/Etuovi listings
    s = parse_sauna("4h+k+2xwc+kph+ph+s")
    assert s and s["present"] and s["private"]
    assert parse_sauna("5h, 2k, at, khh, s-osasto")["private"] is True
    assert parse_parking("5h, 2k, at, khh")["type"] == "garage"
    assert parse_parking("3h + k + ah + s")["type"] == "hall"
    assert parse_toilets("4h+k+2xwc+kph+ph+s") == 2
    assert parse_toilets("4-5 h, k, kph, s, 2 erill.wc") == 2


def test_own_spot_parking():
    assert parse_parking("Jokaiselle asunnolle kuuluu oma autopaikka")["type"] == "own_spot"
    assert parse_parking("Asuntoon kuuluu oma autopaikka")["type"] == "own_spot"
    # a pole beats 'oma' -> still the excluded open_pole case
    assert parse_parking("oma autopaikka lämpötolpalla")["type"] == "open_pole"
    # generic/communal spot stays plain open
    assert parse_parking("autopaikka pihalla")["type"] == "open"


def test_land_ownership():
    assert parse_land_ownership("Oma") == "own"
    assert parse_land_ownership("Oma tontti") == "own"
    assert parse_land_ownership("Vuokra") == "rented"
    assert parse_land_ownership("Vuokratontti") == "rented"
    assert parse_land_ownership("Valinnainen vuokratontti") == "optional_rental"
    assert parse_land_ownership("") is None


def test_property_type():
    assert parse_property_type("Myydään omakotitalo") == "omakotitalo"
    assert parse_property_type("Rivitalo, päätyhuoneisto") == "rivitalo"
    assert parse_property_type("Terraced house near park") == "rivitalo"
    assert parse_property_type("Paritalo") == "paritalo"
    assert parse_property_type("Kerrostalo 3.krs") == "kerrostalo"
    assert parse_property_type("no type here") is None


def test_duplex():
    assert parse_duplex("Tilava kaksikerroksinen rivitaloasunto") is True
    assert parse_duplex("Maisonette apartment") is True
    assert parse_duplex("Yksitasoinen asunto") is None


def test_toilets():
    assert parse_toilets("2 wc, joista erillinen") == 2
    assert parse_toilets("Kaksi wc") == 2
    assert parse_toilets("Erillinen wc ja kylpyhuone") == 2
    assert parse_toilets("wc") == 1
    assert parse_toilets("valoisa koti") is None


if __name__ == "__main__":
    import sys
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in fns:
        try:
            fn(); print(f"PASS {fn.__name__}")
        except AssertionError as e:
            failed += 1; print(f"FAIL {fn.__name__}: {e}")
    sys.exit(1 if failed else 0)
