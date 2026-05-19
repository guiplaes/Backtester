"""
Canvia la contrasenya del dashboard.

Ús (interactiu, la contrasenya s'escriu OCULTA — no es veu mai per pantalla):
    cd "C:\\Users\\Administrator\\Desktop\\MT4 Claude\\grid_manager"
    python change_password.py

Demanara:
  - Nova contrasenya (escriu i Enter; res es veu mentre escrius)
  - Repeteix per confirmar

Guarda nomes el hash bcrypt a .auth_state.json (la contrasenya en clar mai
toca cap fitxer ni cap log).
"""
import getpass
import json
import sys
from pathlib import Path

import bcrypt

AUTH_FILE = Path(__file__).parent / ".auth_state.json"


def main():
    if not AUTH_FILE.exists():
        print(f"ERROR: {AUTH_FILE} no existeix. Restaura primer el sistema d'auth.")
        sys.exit(1)

    try:
        state = json.loads(AUTH_FILE.read_text())
    except Exception as e:
        print(f"ERROR llegint {AUTH_FILE}: {e}")
        sys.exit(1)

    print("Canvi de contrasenya del Grid Portfolio dashboard")
    print("-" * 50)
    print("(Mentre escrius la contrasenya NO es veu res — es normal)")
    print()

    pwd = getpass.getpass("Nova contrasenya: ")
    if not pwd or len(pwd) < 6:
        print("ERROR: contrasenya massa curta (min 6 caracters)")
        sys.exit(1)

    pwd_confirm = getpass.getpass("Repeteix per confirmar: ")
    if pwd != pwd_confirm:
        print("ERROR: les contrasenyes no coincideixen")
        sys.exit(1)

    # Hash amb bcrypt
    state["password_hash"] = bcrypt.hashpw(pwd.encode(), bcrypt.gensalt(12)).decode()
    state.setdefault("created", "")
    state["updated"] = __import__("datetime").datetime.now().isoformat()

    AUTH_FILE.write_text(json.dumps(state, indent=2))

    # Esborra la variable pwd de memoria immediatament
    pwd = None
    pwd_confirm = None

    print()
    print("OK Contrasenya actualitzada.")
    print()
    print("La nova contrasenya queda activa IMMEDIATAMENT — no cal reiniciar streamlit.")
    print("Pots tancar el navegador i tornar a entrar amb la nova contrasenya.")


if __name__ == "__main__":
    main()
