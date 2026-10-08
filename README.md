# C.F. Vilamajor — web automatitzada

- `index.html`: web funcional.
- `data/fcf.json`: dades que llegeix la web.
- `scripts/sync_fcf.py`: consulta la pàgina del club a la FCF, descobreix els equips i actualitza propers partits, últims resultats i classificacions.
- `.github/workflows/sync-fcf.yml`: executa la sincronització cada 6 hores i també manualment.

## Important
La primera execució de GitHub Actions ha de completar-se perquè `data/fcf.json` passi de la plantilla buida a les dades reals.
