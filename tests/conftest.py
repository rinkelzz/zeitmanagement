"""Gemeinsame Testumgebung – wird vor allen Testdateien geladen."""
import os
import tempfile

os.environ["ZM_DATA_DIR"] = tempfile.mkdtemp()
os.environ["ZM_ADMIN_PASSWORD"] = "admin-passwort"
os.environ["ZM_PUBLIC_URL"] = "https://zeit.example.de"
os.environ["ZM_SCHEDULER"] = "0"
os.environ["ZM_UPDATE_CHECK"] = "0"
os.environ["ZM_SELF_RESTART"] = "0"
os.environ["ZM_MODE"] = "team"   # die meisten Tests brauchen mehrere Benutzer
