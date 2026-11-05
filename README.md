# PaperQuick

Rename academic PDFs and copy slide-ready citations directly from Finder on macOS. No Zotero required.

## Install

Requires macOS and Python 3.9+.

```bash
curl -fsSL https://raw.githubusercontent.com/Scarf-0/PaperQuick/main/install.sh | bash
```

## Use

Select one or more PDFs in Finder, right-click, then choose **Services**. On some
macOS versions, Automator workflows may instead be grouped under **Quick Actions**.

- **PaperQuick - Rename PDF** creates names such as `1998 - Winter et al. - Stiffness Control of Balance in Quiet Standing.pdf`.
- **PaperQuick - Copy PPT Citation** copies citations such as `Winter et al., J Neurophysiol, 1998, 80:1211–1221.`

When a match is uncertain, PaperQuick asks for a DOI or full title. It never deletes or overwrites a PDF.

## Privacy

PDFs stay on your Mac. PaperQuick sends only DOI or title search terms to Crossref or OpenAlex.

## Uninstall

```bash
curl -fsSL https://raw.githubusercontent.com/Scarf-0/PaperQuick/main/uninstall.sh | bash
```

## Credits

Inspired by the MIT-licensed [Rename-Academic-PDF](https://github.com/maifeng/Rename-Academic-PDF) and [NameMyPdf](https://github.com/literatecomputing/name-my-pdf) projects.

