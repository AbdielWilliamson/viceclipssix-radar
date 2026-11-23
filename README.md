# Radar ViceClipsSix (nube)

Busca en YouTube, con la API oficial, los videos de GTA que rinden más de lo normal de su canal.
Corre solo en GitHub Actions a las 8:00, 13:00 y 18:00 (hora de Panamá), aunque la PC esté apagada,
y guarda cada informe en `reports/`. La Oficina Virtual los baja al prender la PC y Lucía los revisa.

- `radar_api.py`: el radar. La clave va en el secreto `YOUTUBE_API_KEY`.
- `consultas.json`: qué se busca (noticias y formatos).
- `.github/workflows/radar.yml`: el horario.

No contiene claves ni datos personales.
