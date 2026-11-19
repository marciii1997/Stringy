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
Der Optimierer bewertet einzelne Pixel und die mittlere Helligkeit in 4 × 4
Pixel großen Bereichen. Das Detailgewicht priorisiert lokale Kontraste.
An einer Sackgasse prüft er ausgewählte Verbindungen mit einem zweiten Zug
voraus. Standardmäßig versetzt er in bis zu drei Nachbesserungsrunden frühere Anker der
Route und versucht anschließend erneut, den Pfad zu verlängern. Das Limit
bleibt eine Obergrenze. Bei Gleichstand gewinnt der Anker mit
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

### Canvas mit dem Laser vorbereiten

Öffne im Atelier ein berechnetes Motiv und wähle **Canvas für den Laser
vorbereiten**. Die SVG enthält die Ankerpositionen aus dem berechneten Pfad
in Originalgröße. Anker 0 liegt auf 12 Uhr, die Nummerierung läuft im
Uhrzeigersinn. Die Beschriftung besteht aus Vektorlinien und kann für alle
Anker, in Zehnerschritten, nur für den Start oder ganz ohne Nummern exportiert werden.

Der Durchmesser in der Motivberechnung bezeichnet den **Ankerkreis**. Für ein
rundes Werkstück mit Ø 600 mm und 5 mm Abstand zwischen Ankerzentrum und
Werkstückkante wähle bei der Motivberechnung **590 mm**. Ein bestehendes Motiv
mit 600-mm-Ankerkreis ergibt mit demselben Rand ein Werkstück mit Ø 610 mm.

[Beispieldatei für ein Werkstück mit Ø 600 mm, 360 Ankern und 5 mm Rand](examples/laser/anker-360-werkstueck-600mm.svg)

Die Voreinstellung erzeugt 1 mm breite Markierungskreuze und Gravurnummern.
Alternativ lassen sich kreisförmige Lochkonturen mit wählbarem Nenndurchmesser
erzeugen. Die kreisförmige Außenkontur wird auf Wunsch als eigene Farbe ergänzt.
Importiere die SVG mit 100 % Maßstab. Ordne Schwarz (Markierung), Blau (Nummern),
Rot (Löcher) und Grün (Außenkontur) im Laserprogramm den Bearbeitungsschritten zu.
Beim Einrichten der Lochgröße die Schnittfuge für das eigene Material berücksichtigen.

### Nach einer Fädelpause weitermachen

Im nächsten offenen Türchen zeigt **Fädelmodus** den aktuellen und den nächsten
Anker. **Verbindung gefädelt** speichert einen Schritt im Konto, **Einen Schritt
zurück** korrigiert ihn. Nach einem Gerätewechsel öffnet sich der gespeicherte Stand.
Der Vorschau-Schieberegler lässt sich unabhängig davon bewegen.


### Vorhandene Bilder neu berechnen

Unter **Motive und Simulationen → Simulation öffnen → Parameter ändern und neu berechnen**
kannst du das gespeicherte Originalfoto erneut verwenden. Die neue Variante
erhält eine eigene Nummer. Die vorherige Berechnung lässt sich von dort öffnen.
Bestehende Kalender behalten ihre ursprüngliche Variante und ihren Fortschritt.

Für mehr Rechenaufwand erhöhe die **Nachbesserungsrunden** von 3 auf etwa 8.
Für ein kleines Gesicht im Foto erhöhe die **Vergrößerung** und positioniere
den Ausschnitt über die horizontale und vertikale Bildmitte in Prozent.
Gamma unter 1 hellt die Mitteltöne auf. Verwende für die Fadenstärke den
Durchmesser deines tatsächlichen Fadens. Der Download von JSON und SVG gehört
immer zur gerade geöffneten Berechnung.

### Rekonstruktionsqualität und Referenzvorschau

Optimierer 5 verwendet zunächst einen durchgehenden Pfad. Bei der Nachbesserung darf er
Verbindungen zwischen beliebigen Randnägeln hinzufügen und früher gewählte Verbindungen
entfernen. Ein Spannbaum hält das Netz zusammen. Der Solver repariert ungerade Knotengrade
durch zulässige Verbindungen oder Entnahmen und ordnet die Linien zu einem Eulerpfad.
Er übernimmt den Austausch nur, wenn einschließlich dieser Reparatur die gewichtete
Fehlerfunktion sinkt und alle Verbindungen ins Budget passen. Start, Ende und Ankerabstände
bleiben dabei gültig. Bei sehr kurzen Pfaden nutzt er die lokale Ankerkorrektur.

**Qualitätsvergleich mit feinem Rechenraster** berechnet im Webatelier zwei Kandidaten
mit gleicher Geometrie bei 512 und 1024 Pixeln. Beim feinen Raster schätzt der Solver
die aussichtsreichsten Sehnen über 48 Stützstellen und prüft bis zu 64 davon im genauen
Fadenmodell. Vor einem Suchstopp prüft er auch die übrigen erreichbaren Sehnen.

Für beide Vorschauen zeichnet der Worker die tatsächlich exportierte Ankerfolge bei
2048 × 2048 Pixeln mit Fadenbreite und Deckkraft nach und verkleinert sie auf 1024 Pixel.
Für den Vergleich bringt er beide Ergebnisse auf die gemeinsame 512-Pixel-Vorlage und
glättet sie mit einem Gaußfilter (σ = 2 Pixel). Er misst SSIM und die Wurzel aus dem mittleren
quadratischen Tonwertfehler (RMSE) innerhalb von 95 % des Kreisradius. Markierte Fokusbereiche
erhalten höheres Gewicht. Es gewinnt der höhere Wert von **SSIM minus RMSE**, bei
Gleichstand der kürzere Pfad nach Verbindungszahl. Die Simulation zeigt beide Einzelwerte.
Bei einer Neuberechnung mit gleicher Geometrie und Deckkraft wird ein vorhandener
Vorgängerpfad zum Vergleich ebenfalls gegen die aktuelle Vorlage gemessen.

Mit **0,15 und 0,25 mm vergleichen** im Motiv-Editor erhältst du zwei eigene Simulationen
mit demselben Motiv und unterschiedlichen realen Fadendurchmessern. In den Kalender
gehört der Pfad für den Faden, den du tatsächlich verwendest.

Die Deckkraft und der Fadendurchmesser stammen aus den Motiveinstellungen. Ein Foto
einer real gefädelten Probe unter vergleichbarem Licht wird für eine Materialkalibrierung benötigt.
Für einen schnellen Entwurf lässt sich der zweite Rechenlauf im Formular ausschalten.

Einen früher exportierten Pfad kann die CLI mit demselben Referenzrenderer erneut darstellen:

```sh
cargo run --locked --release -- --replay output/path.json --preview-size 1024 --output vergleich
```

### Bestehende Docker-Installation auf dem PC aktualisieren

Öffne das Terminal im bisherigen Projektordner mit deiner `.env` und der
`compose.yaml`. Verwende für den zuvor beschriebenen lokalen Test diese Befehle:

```sh
git switch main
git pull --ff-only origin main
docker compose build web
docker compose up -d --no-build --force-recreate
docker compose ps -a
```

Der Dienst `migrate` sollte mit `Exited (0)` enden, `web` und `worker` sollten
laufen. Compose verwendet im gleichen Projekt das vorhandene Datenvolume.
Öffne `http://localhost:8000` und melde dich mit deinem bisherigen Admin-Konto an.
Neue Ergebnisse zeigen **Optimierer 5**. Bei Problemen zeigt
`docker compose logs --tail=80 migrate web worker` die Startmeldungen.

## Motive vorbereiten und Fadenpfade vergleichen

Öffne als Atelier-Administrator ein Motiv und wähle **Motiv bearbeiten und Varianten vergleichen**.
Der Server analysiert das gespeicherte Foto lokal mit OpenCV. Er sucht menschliche Gesichter und
Katzenköpfe, gewichtet erkannte Augen und lächelnde Mundpartien, schätzt den Vordergrund mit GrabCut und markiert bei Personen einen möglichen
Kleidungsbereich. Unter **Automatischer Ausschnitt** wird der Kreis um alle erkannten Gesichter
gelegt. Bei Motiven ohne erkanntes Gesicht dient die Vordergrundmaske als Vorschlag. Bei weit auseinanderliegenden Gesichtern kann der Kreis über den Bildrand hinausreichen.

Prüfe die Masken im jeweiligen Pinselwerkzeug. Ergänze fehlende Motivteile, markiere wichtige
Details oder nimm Flächen wieder aus der Auswahl. Die Regler steuern Hintergrund, Kleidung,
Fokus, helle Stellen im Fokus und Konturen. Unter **Hintergrundfarbe** wählst du Weiß, Schwarz oder einen radialen **grauen Verlauf** mit heller Mitte und dunklerem Rand. Die **Hintergrundstärke**
blendet den erkannten Hintergrund und den ergänzten Bildrand stufenlos in die gewählte Farbe.
Bei 0 % bleiben die ursprünglichen Tonwerte erhalten, bei 100 % erhält der vollständig als
Hintergrund markierte Bereich die gewählte Farbe. Mit **Motiv erhalten** kannst du beispielsweise
helle Schnurrhaare aussparen. Die Graustufenvorschau zeigt den Zuschnitt und die Tonwerte. Fokus und Konturen wirken bei der Wahl der tatsächlichen Fäden. Optimierer 3 bewertet
dafür mit feinem und grobem Sobel-Filter erkannte Konturen und gerichtete Helligkeitsunterschiede über einen und vier Rasterpixel.

Mit **Motiv freistellen** wird die Hintergrundstärke auf 100 % gesetzt. Die weiche Motivmaske
bestimmt den Übergang zum neuen Hintergrund. Bei bereits freigestellten PNG- oder WebP-Dateien
übernimmt der Editor die vorhandene Transparenz als Ausgangsmaske.

Unter **Bildstil** erhält **Natürlich** die Fototöne. **Sanft vereinfacht** glättet kleine
Helligkeitsschwankungen innerhalb von Flächen und schont stärkere Kanten. **Plakat** reduziert
das geglättete Motiv auf sechs Graustufen. **Drei Bildstile vergleichen** berechnet mit demselben
Hintergrund, derselben Maske und denselben physischen Einstellungen drei separate Fadenpfade.

**Eigene Variante berechnen** erzeugt einen neuen Fadenpfad. **Sechs Varianten vergleichen**
stellt Original, Fokus mit Lichterschutz, Hintergrund, Konturen, Hintergrund mit Konturen und
die eigene Mischung als separate Worker-Aufträge gegenüber. Ausschnitt und physische Parameter
sind in allen sechs Aufträgen gleich. Öffne ein Ergebnis für die Detailansicht, den Fadenverlauf
oder die Kalenderzuordnung. Bereits zugewiesene Kalender behalten ihren bisherigen Pfad.

Beim Aktualisieren der Docker-Installation im bestehenden Projektordner:

```bash
git switch main
git pull --ff-only origin main
docker compose build web
docker compose up -d --no-build --force-recreate
```

Compose führt die Datenbankmigration automatisch aus. Das benannte Datenvolume enthält die Motive
und Kalender. Die lokale Oberfläche ist unter http://localhost:8000 erreichbar.
