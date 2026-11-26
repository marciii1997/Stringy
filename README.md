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

## Zwei Uploads mit höchstens 350 Nägeln optimieren

Im Projektordner berechnet dieser Befehl die zwei jüngsten Original-Uploads. Für bestimmte
Motive können deren IDs angehängt werden, etwa `optimize_350 12 15`.

```sh
docker compose run --rm --no-deps worker python manage.py optimize_350
```

Der Lauf vergleicht Randnägel, bildabhängige Rand- und Innenpunkte und eine feinere
Berechnung. Alle Varianten eines Bildes verwenden dessen Ausschnitt, Fadenstärke und
Vorbereitung. Die Bewertung berücksichtigt Strukturähnlichkeit, Tonwertfehler,
Abdunklung heller Flächen und modellierte dunkelgraue Nagelköpfe von 3 mm.
Das Programm speichert pro Original den besten Pfad als neues Motiv. Sämtliche
genutzten Nägel sind durchgehend nummeriert und zählen gemeinsam zum Limit.
Am Ende erscheint der Atelierpfad. Ein weiterer Aufruf erkennt vorhandene Ergebnisse.
Die angenommene Kopfgröße und die Vergleichswerte stehen im Fadenpfad-JSON.

## Rechteckige Leinwand und DIN A3

Beim Anlegen eines Motivs oder unter **Parameter ändern und neu berechnen** kannst du
DIN A3 im Hoch- oder Querformat wählen. Unter **Rechteck · eigene Maße** gibst du
Werkstückbreite und -höhe jeweils in Millimetern ein (100 bis 2000 mm). Der Rand bis
zur Mitte der äußeren Nägel beträgt auf jeder Seite 5 mm. A3 hoch ergibt ein
297 × 420 mm großes Werkstück mit einem Nagelfeld von 287 × 410 mm. Beim automatischen
Randplan liegt Nagel 0 oben links. Die Nummerierung folgt dem Umfang im Uhrzeigersinn
und schließt jede Ecke einmal ein. Trage den gemessenen Nagelschaftdurchmesser ein
(Vorgabe 1,2 mm). Der Optimierer hält bei jeder Verbindung auch für einen tangential
geführten Faden mindestens 0,5 mm Abstand zu fremden Schäften. Die Vorschau zeigt
Fäden auf Mittellinien. Kopfgröße, Wickelhöhe und Materialverformung sind separat zu prüfen. Im Motiveditor kannst du den Bildausschnitt vor
der Berechnung verschieben und zoomen. Vorschauen zeigen das bespannte Nagelfeld.
Die Laser-SVG enthält das Werkstück in Originalgröße und auf Wunsch seine Schnittkontur.

Mit der CLI gibt `--rectangle-mm` die **Abstände der äußeren Nagelmitten** an.
Für DIN A3 hoch entspricht das zum Beispiel
`stringy foto.jpg --rectangle-mm 287 410 --anchors 360 --output a3`.
Bei freien Plänen entsprechen [-1, -1] und [1, 1] der oberen linken und der unteren
rechten Ecke des Nagelfelds. Der exportierte Pfad speichert die Geometrie für die Wiedergabe.

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

### Innen-Nägel automatisch bestimmen

Unter **Zusatznägel automatisch platzieren (max.)** gibst du ein Budget für zusätzliche
Nagelpositionen an. Beim neuen Upload sind 24 vorgeschlagen. Mit 0 schaltest du den
Vergleich aus. Randkreis oder eigener Grundplan bleiben die Basis. Die Gesamtgrenze
liegt bei 720 Nägeln, höchstens 96 davon werden automatisch ergänzt.

Der Worker berechnet erst den Grundpfad. Er vergleicht dessen simulierte Helligkeit
mit der zugeschnittenen und bearbeiteten Vorlage. Er sucht räumlich getrennte Punkte
an fehlenden dunklen Details und schlecht getroffenen Konturen und berücksichtigt
markierte Fokusbereiche. Mit diesen Innenpunkten rechnet er den gesamten Pfad erneut,
bei gleichem Faden, Ausschnitt, Verbindungsbudget und gewähltem Raster.
Bei einem Randkreis gilt zwischen seinen ursprünglichen Nägeln weiterhin der
Mindestabstand in Nagelschritten.

Die App übernimmt den Zusatzplan nur bei einem höheren Wert für SSIM minus
Tonwertfehler. Sie entfernt unbenutzte Zusatznägel. Im Fadenverlauf erscheinen
übernommene Innenpunkte orange mit ihren Nummern. **Nagelplan auf der Motivvorlage
ansehen** legt die Positionen über das Motiv. Dieselben IDs und Koordinaten gelten
in Zahlenfolge und Laser-SVG. Beim Neuberechnen sucht die App vom ursprünglichen
Grundplan aus neu. Vorhandene Kalender behalten ihren bisherigen Pfad.

### Freier Nagelplan

Beim Foto-Upload, im Motiv-Editor oder unter **Parameter ändern und neu berechnen**
wählst du unter **Nagelanordnung** die Option **Nägel frei platzieren**. Setze mit
Maus oder Touch 3 bis 720 Punkte im runden Bildausschnitt. Du kannst auch zuerst
die gewünschte Ankerzahl einstellen, den Randkreis übernehmen und dann einzelne
Nägel verschieben oder Innenpunkte ergänzen. Unter **Position in Millimetern**
gibst du die Koordinaten eines neuen oder ausgewählten Nagels exakt ein. Der Faden kann nur die Fläche zwischen
den äußersten Nägeln erreichen. Eine direkte Verbindung durch den Mittelpunkt eines
weiteren Nagels wird bei der Berechnung ausgelassen.

Die Reihenfolge der Punkte bestimmt die Nagelnummern. Die Folge beginnt bei 0.
Im Editor lässt sich ein Nagel durch Antippen oder per Nummer auswählen und entfernen.
Nach dem Entfernen rücken die folgenden Nummern auf. Eine Neuberechnung speichert
den Plan als neue Variante. Der Nagelplan liegt auf der Leinwand, beim Zoomen oder
Verschieben des Fotos bleibt er an derselben Stelle.

Im **Fadenverlauf** blendet **Nägel anzeigen** die Punkte ein. Der aktuelle Nagel
ist farbig hervorgehoben. Im Kalendertürchen siehst du die freien Nägel der bereits
freigeschalteten Fadenfolge. Der Laserexport verwendet alle gespeicherten
Mittelpunkte und prüft die Lochgröße gegen den kleinsten realen Nagelabstand.

Die CLI akzeptiert mit `--nail-positions naegel.json` eine geordnete JSON-Liste
wie `[[0,-1],[0.7,0.5],[-0.7,0.5],[0,0]]`. Der Mittelpunkt ist `[0,0]`, der
Radius entspricht 1, y wächst nach unten. Die Liste legt die Ankerzahl fest.

### Qualitätsvergleich für den Handfädelmodus

Ein normaler Foto-Upload erzeugt einen planen Fadenverlauf mit bis zu 350 Nägeln.
Der Worker beginnt mit dem bisherigen Layout und dessen Pfadverbesserung. Danach
vergleicht er zwei grobe Nagellayouts, rechnet den stärkeren Kandidaten feiner
und probiert bis zu acht neue Positionen an Stellen mit fehlender Deckung.
Jeder Layoutwechsel erhält einen neu berechneten, zusammenhängenden Pfad.
Nach jeder Stufe speichert der Worker den besten vollständigen Pfad. Nach einem
Abbruch kann ein Worker den Auftrag nach Ablauf der sechsminütigen Reservierung
an der nächsten Stufe fortsetzen.

Die Vorschau berücksichtigt die Wickelseite bei jedem Besuch, 1,2-mm-Schäfte,
den angegebenen Fadendurchmesser und 3-mm-Köpfe. Bei der Auswahl zählen
Bildstruktur, helle Flächen und die Zahl der bei 0,5 mm Platzierungsfehler
unsicheren Schritte. Zwei zusätzliche Simulationen bewerten die Empfindlichkeit
gegenüber dünnerem und dickerem Faden. Beim Standardmaterial verwenden sie
0,16 mm mit Deckkraft 0,75 und 0,24 mm mit Deckkraft 1. Die Annahmen beschreiben
ein optisches Modell. Die tatsächliche Wickelhöhe hängt vom verwendeten Material ab.

Unter **Motiv bearbeiten und Berechnung ansehen → Neue Version berechnen**
entsteht ein eigenes Motiv. Bei einem passenden gespeicherten Handfädelplan
beginnt die Suche mit dem vorhandenen Pfad und dessen registrierter Vorlage.
Ein Kalender verweist dauerhaft auf die beim Verschenken ausgewählte Motiv-ID.

Für einen reproduzierbaren Vergleich braucht man das zugeschnittene Zielbild und
zwei JSON-Pfade mit demselben Nagelfeld. Der Befehl schreibt die Messwerte und
einen visuellen Vergleich ins Ausgabeverzeichnis. Für die private Katzenreferenz
ergänzt `--cat` feste Ausschnitte für Augen, Schnauze und Fell. Mit
`--search-seconds 25` lässt sich die Zwei- und Drei-Schnitt-Suche am ersten Pfad
mit demselben Zeitbudget vergleichen.

```bash
python manage.py benchmark_winding --target ziel.png --output vergleich vorher.json nachher.json
cargo test --locked --release
cargo build --locked --release
STRINGY_RELEASE_CHECK=1 python manage.py test
```

Die Django-Tests brauchen wie der laufende Dienst einen gesetzten
`DJANGO_SECRET_KEY`. Der Freigabetest erzeugt sein Testbild selbst und führt
den gebauten Rust-Optimierer aus.

### Vergleich auf einem festen Nagelplan

Bei einer neuen Version eines gespeicherten Handfädelplans prüft der Worker
Wickelseiten, Engstellen und zusammenhängende Fäden vor hellem Hintergrund.
Er kann zwei Verbindungen tauschen oder einen geschlossenen Teil des Pfads
umkehren. Die registrierte Katzenvorlage besitzt feste Bildausschnitte für
beide Augen, Schnauze und Fell. Die Auswahl prüft sie bei 0,16, 0,20 und
0,24 mm Fadenstärke. Das Freigangmodell verwendet 0,5 mm radialen
Platzierungsfehler. Die verbleibenden unsicheren Züge stehen mit Schrittnummer
und benachbartem Nagel im gespeicherten Pfad.

Neue Motive können individuelle Tagesgrenzen tragen. Für deren Aufteilung
schätzt Stringy den Aufwand aus Zuglänge, Wickelrichtungswechsel und erneuten
Besuchen kurz zuvor benutzter Nägel. Gespeicherte Motive ohne Tagesgrenzen
verwenden weiterhin die gleichmäßige Teilung ihrer Zugfolge.

Diese Befehle erzeugen Vergleichsbilder und Messwerte lokal, mit demselben
Rust-Programm wie der Worker. Für eigene Fotos sind die registrierte
Graustufenvorlage und der zugehörige Pfad erforderlich.

```bash
python manage.py winding_trials --verify --output regression
python manage.py winding_trials --route route.json --target target.png --seconds 30 --output suche
python manage.py winding_budgets --route route.json --target target.png --output zugzahlen
```

Im Vergleich mit Zeitlimit zählt pro Suchmethode der beste innerhalb des
Budgets vollständig bewertete Lauf. Auf einem langsameren Rechner ein höheres
Budget wählen. Die drei öffentlichen Testmotive prüfen Porträtkontrast,
Himmelsflächen und feine Konturen. GitHub Actions speichert deren
Vergleichsbilder und Messwerte als Artefakt.
