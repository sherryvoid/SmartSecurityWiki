def test_tree_sitter_java_extracts_two_methods_with_class_and_lines():
    from app.services.parser import chunk_source

    code = """public class Demo {
  public void first() {
    checkPermission();
  }

  private int second() {
    return 2;
  }
}
"""

    chunks = [chunk for chunk in chunk_source("src/Demo.java", "java", code) if chunk.chunk_type == "method"]

    assert len(chunks) == 2
    assert [chunk.symbol for chunk in chunks] == ["first", "second"]
    assert all(chunk.class_name == "Demo" for chunk in chunks)
    assert set(chunks[0].as_dict()) == {
        "chunk_id",
        "file_path",
        "language",
        "class_name",
        "symbol",
        "start_line",
        "end_line",
        "content",
        "chunk_type",
        "tags",
    }
    assert chunks[0].start_line == 2
    assert chunks[0].end_line == 4
    assert chunks[1].start_line == 6
    assert chunks[1].end_line == 8


def test_tree_sitter_go_extracts_function_name_and_lines():
    from app.services.parser import chunk_source

    code = """package auth

func CheckAccess(role string) bool {
    return role == "admin"
}
"""

    chunks = [chunk for chunk in chunk_source("auth/access.go", "go", code) if chunk.chunk_type == "function"]

    assert len(chunks) == 1
    assert chunks[0].symbol == "CheckAccess"
    assert chunks[0].start_line == 3
    assert chunks[0].end_line == 5


def test_python_ast_extracts_class_method_symbol_and_lines():
    from app.services.parser import chunk_source

    code = """class AuthService:
    def require_role(self, user):
        if not user.is_admin:
            raise PermissionError("denied")
"""

    chunks = chunk_source("auth.py", "python", code)
    methods = [chunk for chunk in chunks if chunk.symbol == "require_role"]

    assert len(methods) == 1
    assert methods[0].class_name == "AuthService"
    assert methods[0].start_line == 2
    assert methods[0].end_line == 4


def test_parser_syntax_error_falls_back_without_raising():
    from app.services.parser import chunk_source

    chunks = chunk_source("broken.py", "python", "def broken(:\n    pass\n")

    assert chunks
    assert chunks[0].start_line == 1
    assert chunks[0].end_line >= 1
