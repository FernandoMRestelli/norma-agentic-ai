"""Pruebas unitarias para los hooks de seguridad.

Verifica casos positivos y negativos (fechas, SKUs, montos, exports de WhatsApp y codificaciones).
"""

import sys
from pathlib import Path

# Importar funciones de los scripts de hooks
sys.path.insert(0, str(Path(__file__).parent.parent / ".github" / "scripts"))
from verificar_archivos_prohibidos import es_archivo_prohibido  # noqa: E402
from verificar_telefonos import buscar_telefonos_en_archivo  # noqa: E402


class TestArchivosProhibidos:
    def test_archivos_prohibidos_bloquea_exports_whatsapp_ios(self):
        prohibido, _ = es_archivo_prohibido(Path("data/_chat.txt"))
        assert prohibido
        prohibido, _ = es_archivo_prohibido(Path("_chat.txt"))
        assert prohibido

    def test_archivos_prohibidos_bloquea_exports_whatsapp_android(self):
        prohibido, _ = es_archivo_prohibido(Path("WhatsApp Chat with 1234.txt"))
        assert prohibido
        prohibido, _ = es_archivo_prohibido(Path("Chat de WhatsApp con Juan.txt"))
        assert prohibido
        prohibido, _ = es_archivo_prohibido(Path("data/WhatsApp Chat - Ventas.txt"))
        assert prohibido

    def test_archivos_prohibidos_bloquea_videos_y_heic_fuera_de_tests(self):
        prohibido, _ = es_archivo_prohibido(Path("data/VID-20261027-WA0001.mp4"))
        assert prohibido
        prohibido, _ = es_archivo_prohibido(Path("IMG_1234.heic"))
        assert prohibido
        prohibido, _ = es_archivo_prohibido(Path("video.mov"))
        assert prohibido
        prohibido, _ = es_archivo_prohibido(Path("audio.3gp"))
        assert prohibido

    def test_archivos_prohibidos_case_insensitive_credentials_y_service_accounts(self):
        prohibido, _ = es_archivo_prohibido(Path("Credentials/token.json"))
        assert prohibido
        prohibido, _ = es_archivo_prohibido(Path("my-service_account.json"))
        assert prohibido
        prohibido, _ = es_archivo_prohibido(Path("GOOGLE-SERVICE-ACCOUNT.JSON"))
        assert prohibido

    def test_archivos_prohibidos_permite_multimedia_en_tests(self):
        prohibido, _ = es_archivo_prohibido(Path("tests/fixtures/sample.mp4"))
        assert not prohibido
        prohibido, _ = es_archivo_prohibido(Path("tests/fixtures/audio.opus"))
        assert not prohibido
        prohibido, _ = es_archivo_prohibido(Path("tests/img.png"))
        assert not prohibido

    def test_archivos_prohibidos_permite_env_example_y_diagramas_docs(self):
        prohibido, _ = es_archivo_prohibido(Path(".env.example"))
        assert not prohibido
        prohibido, _ = es_archivo_prohibido(Path("docs/Flujo completo de Norma Agentic AI.png"))
        assert not prohibido


class TestTelefonosArgentinos:
    def test_detecta_telefonos_positivos(self, tmp_path):
        telefonos_prueba = [
            "+54 9 11 1234-5678",
            "5491123456789",  # wa_id / from WhatsApp
            "1112345678",  # AMBA continuo
            "3511234567",  # Córdoba continuo
            "351 4123456",  # Área y número con espacio
            "+54 11 1234-5678",
            "011 1234-5678",
            "(011) 15-1234-5678",
            "15-1234-5678",
            "1512345678",
            "+5491112345678",
        ]

        for idx, tel in enumerate(telefonos_prueba):
            f = tmp_path / f"tel_{idx}.csv"
            f.write_text(f"id,phone\n1,{tel}\n", encoding="utf-8")
            lineas = buscar_telefonos_en_archivo(f)
            assert lineas == [2], f"Fallo al detectar teléfono positivo: {tel}"

    def test_rechaza_falsos_positivos_negativos(self, tmp_path):
        falsos_positivos = [
            "2026-10-29",
            "20261029",  # Semilla / fecha
            "2026-10-27T09:12:00-03:00",
            "ORD-0001",
            "SH-PAN-400",
            "TIN-KOL-61",
            "25000",
            "12500.50",
            "1730245000000",
            "1730245000",
            "cli-001",
            "v0.1.0",
            "96ad3263ac369ef7762e4165fe6be599f6440702",
        ]

        f = tmp_path / "negativos.csv"
        contenido = "id,data\n" + "\n".join(f"{i},{val}" for i, val in enumerate(falsos_positivos))
        f.write_text(contenido, encoding="utf-8")
        lineas = buscar_telefonos_en_archivo(f)
        assert lineas == [], f"Se detectaron falsos positivos indebidos: {lineas}"

    def test_soporta_codificacion_utf16_excel(self, tmp_path):
        f = tmp_path / "excel_utf16.txt"
        f.write_text("cliente\t5491123456789\n", encoding="utf-16")
        lineas = buscar_telefonos_en_archivo(f)
        assert lineas == [1]
