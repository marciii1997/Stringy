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
