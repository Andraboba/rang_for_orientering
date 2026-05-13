from protocols.services.sportident_parser import fetch_sportident_tables, parse_sportident

url = "https://sportident.online/ol_new/?id=2750"
tables, title, err = fetch_sportident_tables(url)
print("err=", err, "tables=", len(tables or []), "title=", title)

parsed = []
base = []
for html in tables or []:
    parsed.extend(parse_sportident(html, base_pairs=base, team_name="*"))

for r in parsed:
    if r["last_name"] in {"Лесовой", "Нестеренко", "Алейников", "Проценко", "Роговченко"}:
        print(r["group_name"], r["place"], "из", r["place_out_of"], ",", r["last_name"], r["first_name"], ", очки:", r["points"])
