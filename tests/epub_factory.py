"""Builds small but real EPUB files for the metadata tests."""
import os
import zipfile
from xml.sax.saxutils import escape

from bookshelf.fs import longpath

CONTAINER = """<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles><rootfile full-path="%s" media-type="application/oebps-package+xml"/></rootfiles>
</container>"""


def make_epub(path, *, title="A Title", authors=("An Author",), language="en", date=None,
              calibre_series=None, series_index=None, collection=None, position=None,
              creator_role=None, opf_dir="OEBPS", pad_bytes=0):
    opf = ["""<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="id">
<metadata xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:opf="http://www.idpf.org/2007/opf">
<dc:identifier id="id">urn:uuid:1234</dc:identifier>"""]
    if title is not None:
        opf.append("<dc:title>%s</dc:title>" % escape(title))
    for a in authors:
        role = ' opf:role="%s"' % creator_role if creator_role else ""
        opf.append("<dc:creator%s>%s</dc:creator>" % (role, escape(a)))
    if language:
        opf.append("<dc:language>%s</dc:language>" % language)
    if date:
        opf.append("<dc:date>%s</dc:date>" % date)
    if calibre_series:
        opf.append('<meta name="calibre:series" content="%s"/>' % escape(calibre_series))
        if series_index is not None:
            opf.append('<meta name="calibre:series_index" content="%s"/>' % series_index)
    if collection:
        opf.append('<meta property="belongs-to-collection" id="c1">%s</meta>' % escape(collection))
        opf.append('<meta refines="#c1" property="collection-type">series</meta>')
        if position is not None:
            opf.append('<meta refines="#c1" property="group-position">%s</meta>' % position)
    opf.append("</metadata><manifest/><spine/></package>")
    if pad_bytes:
        opf.append("<!-- %s -->" % ("a" * pad_bytes))
    opf_path = "%s/content.opf" % opf_dir if opf_dir else "content.opf"
    path = longpath(str(path))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("mimetype", "application/epub+zip")
        z.writestr("META-INF/container.xml", CONTAINER % opf_path)
        z.writestr(opf_path, "\n".join(opf))
    return path
