import json
from pathlib import Path

import pytest

from ingestion.import_models import FetchedDocument, ParseError, ParseFailureCode
from ingestion.jsonld import parse_recipe_jsonld

FIXTURES = Path(__file__).parent / "fixtures"


def document(html: str) -> FetchedDocument:
    return FetchedDocument(
        requested_url="https://example.com/מתכון",
        final_url="https://example.com/מתכון",
        html=html,
        content_type="text/html",
        byte_count=len(html.encode()),
    )


def script(payload: str) -> str:
    return f'<script type="application/ld+json">{payload}</script>'


def test_complete_secondary_recipe_beats_incomplete_main_entity() -> None:
    html = script(
        """{
          "@graph": [
            {"mainEntity": {"@type":"Recipe", "name":"Incomplete"}},
            {"@type":"Recipe", "name":"Complete", "recipeIngredient":["1 cup מים"],
             "recipeInstructions":["Mix"], "prepTime":"PT1H30M", "recipeYield":"4 servings"}
          ]
        }"""
    )

    candidate = parse_recipe_jsonld(document(html))

    assert candidate.title == "Complete"
    assert candidate.servings == 4
    assert candidate.prep_minutes == 90


def test_salvages_overlong_title_and_name_but_preserves_raw_ingredient() -> None:
    title = "כ" * 210
    ingredient = "ingredient " * 30
    html = script(
        f'{{"@type":"Recipe","name":"{title}",'
        f'"recipeIngredient":["{ingredient}"],'
        '"recipeInstructions":["Mix"]}'
    )

    candidate = parse_recipe_jsonld(document(html))

    assert len(candidate.title) == 200
    assert len(candidate.ingredients[0].name) == 200
    assert candidate.ingredients[0].raw_text == ingredient.strip()


def test_reports_incomplete_when_recipe_nodes_exist_but_none_are_eligible() -> None:
    html = script('{"@type":"Recipe","name":"Only"}')

    with pytest.raises(ParseError) as captured:
        parse_recipe_jsonld(document(html))

    assert captured.value.code is ParseFailureCode.INCOMPLETE_RECIPE


def test_reports_no_recipe_when_no_recipe_node_exists() -> None:
    with pytest.raises(ParseError) as captured:
        parse_recipe_jsonld(document(script('{"@type":"Article","name":"Story"}')))

    assert captured.value.code is ParseFailureCode.NO_RECIPE_FOUND


@pytest.mark.parametrize(
    ("fixture", "title", "servings", "instructions"),
    [
        ("recipe-hebrew.html", "מרק כתום", 4, ["חותכים את הירקות.", "מבשלים וטוחנים."]),
        ("recipe-english.html", "Herb Rice", 2, ["Cook the rice.", "Fold in the herbs."]),
        ("recipe-mixed.html", "Chicken עם לימון", 3, ["Season the chicken.", "אופים עד שמוכן."]),
    ],
)
def test_normalizes_hebrew_english_and_mixed_fixtures(
    fixture: str,
    title: str,
    servings: int,
    instructions: list[str],
) -> None:
    candidate = parse_recipe_jsonld(document((FIXTURES / fixture).read_text("utf-8")))

    assert candidate.title == title
    assert candidate.servings == servings
    assert candidate.instructions == instructions


def test_cleans_markup_entities_and_whitespace() -> None:
    html = script(
        """{"@type":"Recipe","name":"  Tomato &amp; <b>Herb</b>  ",
        "recipeIngredient":["  2&nbsp; tomatoes  ", ""],
        "recipeInstructions":[{"text":" Mix   <i>gently</i> "}, {"text":"  "}]}"""
    )

    candidate = parse_recipe_jsonld(document(html))

    assert candidate.title == "Tomato & Herb"
    assert [item.raw_text for item in candidate.ingredients] == ["2 tomatoes"]
    assert candidate.instructions == ["Mix gently"]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("4 servings", 4),
        (5, 5),
        ("serves 4-6", None),
        (["4 servings", "6 servings"], None),
        ("family size", None),
        (0, None),
    ],
)
def test_normalizes_only_unambiguous_positive_servings(value: object, expected: int | None) -> None:
    html = script(
        """{"@type":"Recipe","name":"Yield","recipeIngredient":["salt"],
        "recipeInstructions":["Mix"],"recipeYield":VALUE}""".replace("VALUE", json.dumps(value))
    )

    assert parse_recipe_jsonld(document(html)).servings == expected


def test_normalizes_durations_and_omits_calendar_or_invalid_values() -> None:
    html = script(
        """{"@type":"Recipe","name":"Timing","recipeIngredient":["salt"],
        "recipeInstructions":["Mix"],"prepTime":"PT1H30M","cookTime":"PT1S",
        "totalTime":"P1M"}"""
    )

    candidate = parse_recipe_jsonld(document(html))

    assert candidate.prep_minutes == 90
    assert candidate.cook_minutes == 1
    assert candidate.total_minutes is None


def test_omits_duration_that_exceeds_timedelta_range() -> None:
    html = script(
        """{"@type":"Recipe","name":"Timing","recipeIngredient":["salt"],
        "recipeInstructions":["Mix"],"totalTime":"P999999999999D"}"""
    )

    assert parse_recipe_jsonld(document(html)).total_minutes is None


def test_flattens_nested_instruction_shapes_without_splitting_blobs() -> None:
    html = script(
        """{"@type":"Recipe","name":"Steps","recipeIngredient":["salt"],
        "recipeInstructions":["First. Second.",{"@type":"HowToSection","itemListElement":[
          {"@type":"HowToStep","text":"Third"},
          {"@type":"HowToSection","itemListElement":{"text":"Fourth"}}
        ]}]}"""
    )

    assert parse_recipe_jsonld(document(html)).instructions == [
        "First. Second.",
        "Third",
        "Fourth",
    ]


def test_splits_keywords_and_deduplicates_tags_without_changing_first_casing() -> None:
    long_tag = "x" * 65
    html = script(
        f'''{{"@type":"Recipe","name":"Tags","recipeIngredient":["salt"],
        "recipeInstructions":["Mix"],"keywords":"Dinner, Israeli, dinner",
        "recipeCategory":["Main", "ISRAELI"],"recipeCuisine":["Levant", "{long_tag}"]}}'''
    )

    assert parse_recipe_jsonld(document(html)).tags == [
        "Dinner",
        "Israeli",
        "Main",
        "Levant",
    ]


def test_ranks_main_entity_then_optional_fields_then_document_order() -> None:
    html = script(
        """{"@graph":[
        {"@type":"Recipe","name":"First","recipeIngredient":["salt"],
         "recipeInstructions":["Mix"],"prepTime":"PT5M","cookTime":"PT5M"},
        {"@type":"Recipe","name":"Second","recipeIngredient":["salt"],
         "recipeInstructions":["Mix"],"prepTime":"PT5M","cookTime":"PT5M"},
        {"mainEntity":{"@type":"Recipe","name":"Main","recipeIngredient":["salt"],
         "recipeInstructions":["Mix"]}}
        ]}"""
    )

    assert parse_recipe_jsonld(document(html)).title == "Main"


def test_optional_field_count_breaks_ties_before_document_order() -> None:
    html = script(
        """[{"@type":"Recipe","name":"First","recipeIngredient":["salt"],
        "recipeInstructions":["Mix"]},
        {"@type":"Recipe","name":"Richer","recipeIngredient":["salt"],
        "recipeInstructions":["Mix"],"prepTime":"PT5M","recipeYield":2}]"""
    )

    assert parse_recipe_jsonld(document(html)).title == "Richer"


def test_oversized_duration_does_not_crash_parser() -> None:
    # A >4300-digit duration would raise ValueError under Python's int-string
    # limit if int() ran unguarded; the field must degrade to null instead.
    huge = "PT" + "9" * 5000 + "S"
    html = script(
        f"""{{"@type":"Recipe","name":"Timing","recipeIngredient":["salt"],
        "recipeInstructions":["Mix"],"prepTime":"{huge}"}}"""
    )

    candidate = parse_recipe_jsonld(document(html))

    assert candidate.prep_minutes is None


def test_main_entity_by_id_reference_wins_over_richer_decoy() -> None:
    # The common @graph shape: mainEntity is an @id reference to a sibling node,
    # not an inline object. The referenced recipe must be selected even when a
    # decoy recipe scores higher on optional fields.
    html = script(
        """{"@graph":[
        {"@type":"WebPage","mainEntity":{"@id":"#recipe"}},
        {"@type":"Recipe","@id":"#recipe","name":"Real",
         "recipeIngredient":["salt"],"recipeInstructions":["Mix"]},
        {"@type":"Recipe","name":"Decoy","recipeIngredient":["salt"],
         "recipeInstructions":["Mix"],"prepTime":"PT5M","recipeYield":4}
        ]}"""
    )

    assert parse_recipe_jsonld(document(html)).title == "Real"


def test_main_entity_flag_does_not_leak_to_item_list_members() -> None:
    # mainEntity pointing at an ItemList must not promote the list's recipe
    # members over a genuine standalone recipe.
    html = script(
        """{"@graph":[
        {"mainEntity":{"@type":"ItemList","itemListElement":[
          {"@type":"Recipe","name":"ListedA","recipeIngredient":["salt"],
           "recipeInstructions":["Mix"]},
          {"@type":"Recipe","name":"ListedB","recipeIngredient":["salt"],
           "recipeInstructions":["Mix"]}]}},
        {"@type":"Recipe","name":"Standalone","recipeIngredient":["salt"],
         "recipeInstructions":["Mix"],"prepTime":"PT5M","recipeYield":2}
        ]}"""
    )

    assert parse_recipe_jsonld(document(html)).title == "Standalone"
