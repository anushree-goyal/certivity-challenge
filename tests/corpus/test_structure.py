from certivity.corpus import structure as st


def judge(label, previous, previous_line="", rest="Text"):
    return st.judge_label(st.parse_label(label), st.parse_label(previous) if previous else None, previous_line, rest)


def test_label_sequence_accepts_children_and_siblings():
    assert judge("6.10.1.", "6.10.").accept
    assert judge("6.10.5.", "6.10.4.2.").accept
    assert judge("7.", "6.10.4.2.").accept
    assert judge("6.10.", "6.9.9.2.").accept and judge("6.10.", "6.9.9.2.").flags == ()


def test_cross_reference_at_line_start_is_not_a_label():
    # R48 p.50: "(measured according to the provisions of paragraph\n5.8.1. above) the upward angle"
    assert not judge("5.8.1.", "6.10.5.1.", "measured according to the provisions of paragraph", "above) the upward").accept
    assert not judge("5.8.1.", "6.10.5.1.", "", "above) the upward").accept


def test_list_item_after_comma_is_still_a_label_when_in_sequence():
    # R150 Annex 6 Part 6: "3.1. in case of ... 500 hours," then "3.2. in case of ..."
    assert judge("3.2.", "3.1.", "3.1. in case of retro-reflective devices for a period of 500 hours,", "in case of").accept


def test_out_of_sequence_capitalised_label_is_kept_but_flagged():
    verdict = judge("1.", "2.2.", "Notes:", "This drawing represents")
    assert verdict.accept and verdict.flags == ("label_out_of_sequence",)


def test_label_regex_requires_trailing_dot():
    assert st.LABEL_LINE.match("6.10.4.2. In height: above the ground")
    assert not st.LABEL_LINE.match("60 ± 2 60 ± 2")
    assert not st.LABEL_LINE.match("10 m")
    assert st.LABEL_LINE_NODOT.match("5.7 Grouped, combined or reciprocally incorporated")


def test_annex_heading_namespaces():
    def ns(text):
        m = st.ANNEX_HEADING.match(text)
        return st.namespace_label(m["annex"], m["part"], m["appendix"] or (" " if m["appx_kw"] else None))

    assert ns("Annex 4 - Appendix 1") == "Annex 4 Appendix 1"
    assert ns("Annex 4 Appendix 2 Measurement equipment") == "Annex 4 Appendix 2"
    assert ns("Annex 6 - Part 6 - Appendix 1 Title") == "Annex 6 Part 6 Appendix 1"
    assert ns("Annex 2A Information document") == "Annex 2A"
    assert ns("Annex 5 - Appendix RETRO-REFLECTORS FOR TRAILERS") == "Annex 5 Appendix"


def test_citation_label_matches_expected_format():
    assert st.citation_label(st.MAIN, "6.10.4.2.") == "6.10.4.2"
    assert st.citation_label("Annex 6 Part 6", "3.") == "Annex 6 Part 6 3"
    assert st.citation_label("Annex 4", None) == "Annex 4"


def test_reference_extraction():
    refs = st.find_references("as specified in paragraph 5.1.3.2.2. of this Regulation and paragraph 2.1 of Annex 4")
    assert [(r.target_paragraph, r.target_annex, r.explicit_this_regulation) for r in refs] == [("5.1.3.2.2", None, True), ("2.1", "Annex 4", False)]
    refs = st.find_references("in the meaning of paragraphs 2.2.1.1. to 2.2.1.4., but")
    assert refs[0].target_paragraph == "2.2.1.1-2.2.1.4"
    refs = st.find_references("Examples ... can be found in Annex 3, Part 7.")
    assert refs[0].target_annex == "Annex 3 Part 7"
    refs = st.find_references("measured as specified in Part 2 of Annex 6")
    assert refs[0].target_annex == "Annex 6 Part 2"
    refs = st.find_references("Regulations Nos. 37, 99 and 128 respectively")
    assert [r.target_regulation_number for r in refs] == ["37", "99", "128"]
    refs = st.find_references("paragraph 6.2 of UN Regulation No. 10")
    assert (refs[0].target_paragraph, refs[0].target_regulation_number) == ("6.2", "10")


def test_definition_terms():
    assert st.definition_terms('"Rear position lamp" means the lamp used to indicate') == ["Rear position lamp"]
    assert st.definition_terms('“Manufacturer logo” means a graphic mark') == ["Manufacturer logo"]
    assert st.definition_terms('"Extreme outer edge" on either side of the vehicle, means the plane') == ["Extreme outer edge"]
    assert st.definition_terms("The lamp shall be red.") == []


def test_regulation_hint():
    assert st.regulation_number_from_hint("UN R48") == "48"
    assert st.regulation_number_from_hint("Regulation No. 13H") == "13H"
    assert st.regulation_number_from_hint("UN GTR 9") is None
