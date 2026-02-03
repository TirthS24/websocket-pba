from os.path import splitext
from pathlib import Path
from typing import Any, List

def _sanitize(name: str) -> str:
    name = splitext(name)[0]
    name = name.replace('-', '_').replace(' ', '_')
    if not name.isidentifier():
        name = f"x_{name}"
    return name

class _TextNode:
    """
        Internal helper node for the TextStore tree.

        Stores a mapping of children (either more _TextNode instances or actual text leaves),
        and enables dot-style attribute access to child items.

        Attributes:
            _children: Dictionary mapping sanitized names to children (_TextNode or text content).
    """
    def __init__(self):
        self._children: dict = {}

    def __getattr__(self, name) -> Any:
        if name in self._children:
            return self._children[name]
        raise AttributeError(f"No such attribute: {name}")

    def __setattr__(self, name, value) -> None:
        if name.startswith("_"):
            # don't populate _children dict with internal attrs
            return super().__setattr__(name, value)
        self._children[name] = value

    def __repr__(self) -> str:
        return f"<TextNode keys={list(self._children.keys())}>"

class TextStore:
    """
        In-memory tree of text files in a directory.

        After instantiating with a root folder, all .txt files in that folder (recursively)
        can be accessed as attributes corresponding to their directory structure:
            store.a.b.somefile -> contents of /root/a/b/somefile.txt

    """

    def __init__(self, root_folder: Path | str) -> None:
        if root_folder:
            self._root = _TextNode()
        if isinstance(root_folder, (str, Path)):
            root = Path(root_folder)
        else:
            raise TypeError("root_folder must be a str or pathlib.Path object")

        self._populate(root, self._root)


    def _populate(self, cur_folder: Path, cur_node: _TextNode) -> None:
        for item in cur_folder.iterdir():
            clean_name = _sanitize(item.name)
            if item.is_dir():
                child_node = _TextNode()
                setattr(cur_node, clean_name, child_node)
                self._populate(item, child_node)

            elif item.is_file() and item.suffix == ".txt":
                with item.open('r', encoding='utf-8') as f_in:
                    content = f_in.read().strip()
                setattr(cur_node, clean_name, content)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._root, name)

    def __repr__(self) -> str:
        if hasattr(self, "_root"):
            keys = list(self._root._children.keys())
        else:
            keys = []
        return f"<TextStore keys={keys}>"

    def list_paths(self) -> List[str]:
        """List all dot-paths to actual text content within this store."""

        def _recurse(node: _TextNode, prefix: List[str]) -> List[str]:
            paths = []
            for key, value in node._children.items():
                if isinstance(value, _TextNode):
                    paths += _recurse(value, prefix + [key])
                else:
                    # leaf: must be str (the text)
                    paths.append('.'.join(prefix + [key]))
            return paths

        return _recurse(self._root, [])