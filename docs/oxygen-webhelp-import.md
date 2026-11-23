# Import the JMP DITA sources

This process converts the JMP DITA source folder, the XML that Oxygen edits and
publishes, into staged, authorable content for the JMP Edge Delivery site. It
starts at the root DITA map, follows the map hierarchy to each topic, and renders
the topic XML as plain HTML. It does not serve Oxygen's generated page shell,
search runtime, or CSS.

The converter preserves topic hierarchy, headings, lists, definition lists,
tables, code, notes, figures, local images and file links, page anchors,
keyref text, conref content, titles, and short descriptions. It creates a help
landing page, per-folder topic indexes, a legacy URL map, and reports for broken
local references, unhandled elements, and missing image alternative text. The
site already has a search block; the generated landing page uses its
`/query-index.json` source.

The converter reads the sources only. It does not upload content, publish pages,
change redirects, or modify the source folder.

## Stage the content

Python 3.9 or newer is required; the converter uses only the standard library.
Run it from the Edge Delivery repository:

```sh
python3 scripts/convert_oxygen_webhelp.py \
  --source /path/to/jmphelpxml \
  --output import-work \
  --prefix /help
```

`--source` is the DITA folder that contains the root map. By default the root map
is `JMP_Documentation_Library.ditamap`; use `--map` to choose another map relative
to `--source`. Only topics referenced by that map are converted. Other XML files,
such as conref sources in `reuse/`, are counted in `summary.json` but not staged.

For a small smoke test, add `--limit 5`. The output folder must be outside the
source folder and empty unless `--force` is provided. `--force` overwrites
generated files but does not delete stale files.

The output is organized as:

```text
import-work/
  help/
    index.html
    catalog/
      UG.html
    UG/
      UG_ColumnInfo_numericFormats.html
      Images/
        referenced-image.png
  _migration/
    accessibility.csv
    legacy-url-map.csv
    manifest.csv
    summary.json
    unhandled-elements.csv
    unresolved-links.csv
```

Only upload the `help/` tree to the matching `/help/` folder in Dark Alley.
Keep `_migration/` as a local review artifact.

## Preview and review

With the AEM CLI installed, start a local preview from the repository root:

```sh
aem up --html-folder import-work
```

Open `/help/index` and inspect representative pages containing notes, figures,
tables, lists, equations, and code. Check `_migration/unresolved-links.csv` for
missing assets, links that were not converted, and anchors that do not exist.
Check `_migration/unhandled-elements.csv`; a non-empty file lists DITA elements
the converter does not map yet, whose content was kept as plain text. Check
`_migration/accessibility.csv` for images without alternative text. In the
current sources only a small share of images have `<alt>` text, so expect that
report to be long and plan an authoring pass for it. Confirm the generated paths
in `legacy-url-map.csv` with the site owner, then configure redirects separately
before switching any existing public URLs.

The DITA sources do not carry robots metadata. Generated pages use the
`--robots` value, which defaults to `noindex, follow` so draft content is not
indexed. Keep the default while reviewing. If the help pages should become
searchable and indexable on launch, pass a different `--robots` value only after
editorial approval.

After review, transfer the `help/` tree to Dark Alley, preview the pages on the
site's preview environment, and publish through the normal content workflow.
There is no automatic production publish step.

Run the converter's focused tests with:

```sh
python3 -m unittest discover -s test/scripts -p 'test_oxygen_webhelp_import.py' -v
```

## Conversion boundaries

- Only topics reachable from the root map are converted. `<draft-comment>`
  content, prolog metadata, and key definitions are not published.
- Conrefs are resolved in place from their source file. Keyrefs are replaced with
  the key's text. A key that is not defined is reported.
- Internal links to converted topics are rewritten to extensionless Edge
  Delivery paths under the prefix. Links to topics that are not in the root map
  keep their text, are not linked, and are reported.
- Only locally referenced assets are copied; missing local references are
  listed in `unresolved-links.csv`.
- LaTeX equations are emitted as code text, not rendered. Review equation pages
  before publishing.
- The legacy URL map assumes the previous WebHelp output named each topic after
  its source file with a `.html` extension. Confirm this with the site owner.
- Existing URLs change to the `/help/` prefix by default. Review the legacy URL
  map and configure redirects if old URLs must continue to work.
