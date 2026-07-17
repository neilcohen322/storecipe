import json

from ingestion.jsonld import discover_recipe_nodes


def test_discovers_recipe_in_graph_and_marks_main_entity() -> None:
    html = """
    <script type="application/ld+json">
    {"@context":"https://schema.org","@graph":[
      {"@type":"BreadcrumbList","name":"crumbs"},
      {"mainEntity":{"@type":["Thing","https://schema.org/Recipe"],
       "name":"מרק","recipeIngredient":["מים"],
       "recipeInstructions":[{"@type":"HowToStep","text":"לבשל"}]}}
    ]}
    </script>
    """

    recipes = discover_recipe_nodes(html)

    assert len(recipes) == 1
    assert recipes[0].node["name"] == "מרק"
    assert recipes[0].is_main_entity is True


def test_strips_whole_script_comment_and_cdata_wrappers() -> None:
    html = """
    <script type="application/ld+json"><!--
      {"@type":"Recipe","name":"One"}
    --></script>
    <script type="application/ld+json"><![CDATA[
      {"@type":"Recipe","name":"Two"}
    ]]></script>
    """

    assert [item.node["name"] for item in discover_recipe_nodes(html)] == ["One", "Two"]


def test_ignores_pathological_block_and_keeps_valid_sibling_block() -> None:
    nested: object = {"@type": "Recipe", "name": "too deep"}
    for _ in range(65):
        nested = [nested]
    html = (
        f'<script type="application/ld+json">{json.dumps(nested)}</script>'
        '<script type="application/ld+json">'
        '{"@type":"Recipe","name":"valid"}'
        "</script>"
    )

    assert [item.node["name"] for item in discover_recipe_nodes(html)] == ["valid"]


def test_discovers_top_level_arrays_and_nested_short_or_full_types_in_order() -> None:
    html = """
    <script type="application/ld+json">
    [
      {"nested":{"@type":"https://schema.org/Recipe/","name":"First"}},
      {"@type":["Thing","Recipe"],"name":"Second"}
    ]
    </script>
    """

    recipes = discover_recipe_nodes(html)

    assert [item.node["name"] for item in recipes] == ["First", "Second"]
    assert [item.order for item in recipes] == [0, 1]


def test_ignores_malformed_json_and_embedded_comments_beside_valid_block() -> None:
    html = """
    <script type="application/ld+json">{"@type":"Recipe", broken}</script>
    <script type="application/ld+json">
      {"@type":"Recipe", /* comment */ "name":"invalid"}
    </script>
    <script type="application/ld+json">
      {"@type":"Recipe","name":"valid"}
    </script>
    """

    assert [item.node["name"] for item in discover_recipe_nodes(html)] == ["valid"]


def test_ignores_non_jsonld_scripts_and_non_recipe_types() -> None:
    html = """
    <script type="application/json">{"@type":"Recipe","name":"wrong type"}</script>
    <script type="application/ld+json">{"@type":"Article","name":"not recipe"}</script>
    """

    assert discover_recipe_nodes(html) == []
