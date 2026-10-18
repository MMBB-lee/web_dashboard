"""Prepare local HTML originals for E's upstream text/PDF/Markdown parser."""
from contextlib import contextmanager
from html.parser import HTMLParser
from pathlib import Path
import re
from tempfile import TemporaryDirectory


def read_document(file_path):
    path=Path(file_path)
    if path.suffix.lower() not in {'.html','.htm'}:
        from intelligent_analysis.knowledge.parse import read_document as upstream_read
        return upstream_read(path)

    class TextParser(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.ignored=0;self.parts=[]
        def handle_starttag(self,tag,attrs):
            if tag in {'script','style','noscript'}: self.ignored+=1
            if not self.ignored and tag in {'p','div','br','tr','li','h1','h2'}: self.parts.append('\n')
        def handle_endtag(self,tag):
            if tag in {'script','style','noscript'} and self.ignored: self.ignored-=1
            if not self.ignored and tag in {'p','div','tr','li','h1','h2'}: self.parts.append('\n')
        def handle_data(self,text):
            if not self.ignored: self.parts.append(text)
    parser=TextParser()
    parser.feed(path.read_text(encoding='utf-8'))
    parser.close()
    return re.sub(r'[\ud800-\udfff]','\ufffd',''.join(parser.parts)).strip()


@contextmanager
def prepared_source(file_path,data_dir):
    source=Path(file_path)
    if source.suffix.lower() not in {'.html','.htm'}:
        yield str(source)
        return
    root=(Path(data_dir)/'knowledge_input').resolve()
    root.mkdir(parents=True,exist_ok=True)
    with TemporaryDirectory(prefix='html-',dir=root) as temporary:
        folder=Path(temporary).resolve()
        assert folder.is_relative_to(root)
        text=folder/'extracted.md'
        text.write_text(read_document(source),encoding='utf-8')
        yield str(text)
