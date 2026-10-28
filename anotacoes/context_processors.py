from .models import UserPreference


def accent_preference(request):
    if not request.user.is_authenticated:
        values = {}
    else:
        values = UserPreference.objects.filter(user=request.user).values_list("values", flat=True).first() or {}
    accent = values.get("accent", "indigo")
    if accent not in {"indigo", "violeta", "verde", "rosa", "ambar"}:
        accent = "indigo"
    try:
        font = int(values.get("font", 1))
    except (TypeError, ValueError):
        font = 1
    if font not in {0, 1, 2}:
        font = 1
    # A opção de idiomas usa este sinal para priorizar a preferência da conta
    # e recorrer ao armazenamento local somente em páginas sem autenticação.
    return {
        "accent_preference": {"accent": accent},
        "language_preference": {
            "authenticated": request.user.is_authenticated,
            "lang": values.get("lang", "pt") if values.get("lang") in {"pt", "en"} else "pt",
        },
        "accessibility_preference": {
            "font": font,
            "contrast": bool(values.get("contrast", False)),
            "motion": bool(values.get("motion", False)),
        },
    }