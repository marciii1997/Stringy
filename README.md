# Stringy
Stringart-Robot Algorithm with UI running in Webbrowser with backend on Ubutu Server inside Docker

## String-Art-Algorithmus

Der Kommandozeilen-Prototyp liest JPEG, PNG oder WebP, richtet das Bild bei Bedarf
anhand seiner EXIF-Daten aus und erstellt aus dem mittigen quadratischen Ausschnitt
eine kreisförmige Graustufenvorlage. Transparenz wird auf Weiß gelegt.

Der Prototyp benötigt Rust 1.89 oder neuer.

```bash
cargo run --release --locked -- katze.jpg --output output
```

Standardmäßig verwendet der Prototyp 360 Anker, einen Canvas-Durchmesser von
600 mm, schwarzen Faden mit 0,25 mm Durchmesser auf Weiß, maximal 12.000
Verbindungen und ein Rechenraster mit 512 × 512 Pixeln. Der Anker 0 liegt auf 12 Uhr. Die Nummerierung
verläuft im Uhrzeigersinn. Optionen stehen unter `--help`.

Der Optimierer bewertet in jedem Schritt die vom aktuellen Anker ausgehenden
Sehnen parallel. Er wählt anhand des quadrierten Graustufenfehlers und einer
kleinen Strafe für wiederholte Sehnen. Der Helligkeitsgewinn berücksichtigt,
dass weitere Fäden über bereits dunklen Stellen weniger verändern.
Wenn kein zulässiger Schritt den bewerteten Fehler weiter reduziert, endet
der Pfad vor Erreichen der Obergrenze. Bei Gleichstand gewinnt der Anker mit
der kleineren Nummer. Der Cache speichert symmetrische Sehnen einmal und
berechnet Sehnen bei ausgeschöpftem Speicherbudget bei Bedarf.

| Datei | Inhalt |
| --- | --- |
| `target.png` | Graustufenvorlage nach Zuschnitt |
| `preview.png` | Simulation der Fadenüberlagerung auf weißem Grund |
| `path.json` | Geordnete Ankerfolge, Koordinaten in mm relativ zur Mitte (+y nach unten), Einstellungen und Fehlermaße |
| `segments.csv` | Reihenfolge der Segmente mit Startanker, Endanker und Sehnenlänge in mm |

Die Längenangaben stammen aus der Kreisgeometrie. Für den Bau kommen
Fadenreserven für Befestigung und Umlenkung an den Ankern hinzu. Die Vorschau
modelliert die optische Abdeckung bei der gewählten Rasterauflösung.

```bash
cargo test --locked
```

## Webatelier und Adventskalender

Im Webatelier verwaltet eine Administratorin oder ein Administrator persönliche
Zugänge, Motive und Adventskalender. Jeder Kalender gehört zu einem Konto,
einem Motiv und einem Adventsjahr. Ein Konto kann mehrere Kalender erhalten.
Empfänger sehen ihren Kalendernamen, die Ankeranordnung und ihre freigegebenen
Fadenabschnitte. Motivname, Vorlage, vollständige Bildvorschau und Pfadexport
sind dem Atelier vorbehalten.

### Auf dem V-Server starten

Voraussetzungen sind Docker mit Compose, eine Domain und ein HTTPS-Reverse-Proxy.
Die Anwendung lauscht über Compose auf dem Loopback-Port 8000. Leite die Domain
im Proxy dorthin und lasse den Proxy den Header `X-Forwarded-Proto` setzen.
Für Caddy auf demselben Host genügt zum Beispiel dieser Site-Block mit deiner Domain.

```caddyfile
atelier.example.org {
    reverse_proxy 127.0.0.1:8000
}
```

Lege im Checkout einmalig eine private `.env` an. Ersetze die Beispieldomain.
Der folgende Befehl bricht ab, falls die Datei bereits existiert.

```sh
(umask 077; set -C; printf 'DJANGO_SECRET_KEY=%s\nSTRINGY_HOSTS=atelier.example.org\n' "$(openssl rand -hex 48)" > .env)
docker compose up --build -d
docker compose exec web python manage.py createsuperuser
```

Öffne danach deine Domain und melde dich mit diesem Administratorkonto an.
Für einen lokalen HTTP-Test verwende in einer separaten Testumgebung
`STRINGY_HTTPS=0` und `STRINGY_HOSTS=localhost,127.0.0.1`.
Für den öffentlichen HTTPS-Betrieb sind sichere Cookies standardmäßig aktiv.

### Einen Kalender verschenken

1. Lege über **Account anlegen** einen persönlichen Zugang mit Passwort an.
   Für Beschenkte genügen die Standardeinstellungen ohne Mitarbeiterrechte.
2. Wähle **Neues Bild umwandeln**, vergib einen internen Motivnamen und lade
   das Foto hoch. Ein separater Worker berechnet den Fadenpfad im Hintergrund.
   Nach dem Speichern öffnet sich die Statusseite und wechselt automatisch zur fertigen Simulation.
3. Öffne die Simulation. Vergleiche die Graustufenvorlage mit der fertigen
   Vorschau und prüfe den Verlauf über den Schieberegler. Für einen Kalender
   braucht das Motiv mindestens 24 Verbindungen.
4. Wähle **Kalender mit diesem Motiv anlegen**, die beschenkte Person, das Jahr
   und einen Kalendernamen, der das Motiv geheim hält. Teile ihr den Zugang mit.
5. Vom 1. bis 24. Dezember wird täglich um 00:00 Uhr in `Europe/Berlin` ein
   Abschnitt freigeschaltet. Die Anker sind ab 0 oben im Uhrzeigersinn nummeriert.
   Jede Tagesfolge beginnt am Endanker des Vortags. In der Tagesansicht kann
   die Person die Simulation schrittweise ansehen und den Tag als fertig markieren.
   Bereits freigeschaltete Türchen bleiben auch nach Weihnachten zugänglich.

SQLite, Fotos, Ergebnisse und Fortschritt liegen gemeinsam im Docker-Volume
`studio-data`. Für eine konsistente Dateisicherung Webdienst und Worker kurz
stoppen oder die SQLite-Backup-Funktion verwenden. Bewahre den Schlüssel aus
`.env` separat auf. Je Worker läuft eine Berechnung gleichzeitig.
Unterbrochene Jobs werden nach sechs Minuten erneut aufgenommen.
Fehlgeschlagene Motive lassen sich in der Motivliste erneut einreihen.

### Webtests

```sh
docker compose exec web python manage.py test studio
```
