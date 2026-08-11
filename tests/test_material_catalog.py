from app.services.material_catalog import (
    format_materials_message,
    normalize_employment_type,
    required_materials_for_employment_type,
)


def test_social_hire_materials_include_resignation_certificate() -> None:
    materials = required_materials_for_employment_type("社招")

    assert any(item.code == "resignation_certificate" for item in materials)
    assert "离职证明" in format_materials_message("社招")


def test_domestic_campus_materials_include_employment_agreement() -> None:
    materials = required_materials_for_employment_type("校招-境内")

    assert normalize_employment_type("校招-境内") == "campus_domestic"
    assert any(item.code == "resume" for item in materials)
    assert any(item.code == "employment_agreement" for item in materials)
    assert "求职简历" in format_materials_message("校招-境内")


def test_overseas_campus_materials_include_overseas_certification() -> None:
    materials = required_materials_for_employment_type("校招-境外")

    assert normalize_employment_type("校招-境外") == "campus_overseas"
    assert any(item.code == "overseas_degree_certification" for item in materials)
