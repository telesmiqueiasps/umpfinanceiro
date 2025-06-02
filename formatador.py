def formatar_moeda(valor, simbolo=True):
    """
    Formata o valor como moeda brasileira.
    """
    try:
        import locale
        locale.setlocale(locale.LC_ALL, 'pt_BR.UTF-8')  # opcional, evita erro se locale for usado
        return locale.currency(float(valor), symbol=simbolo, grouping=True)
    except:
        try:
            valor = float(valor)
        except (ValueError, TypeError):
            valor = 0.0
        simb = 'R$' if simbolo else ''
        val = f"{valor:,.2f}".replace(",", "v").replace(".", ",").replace("v", ".")
        return f"{simb} {val}".strip()



