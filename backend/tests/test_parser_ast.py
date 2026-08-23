# READ SUMMARY: This test module verifies parser chunk extraction for Java, Go, Python, and endpoint metadata.
# CHANGED: Added endpoint chunking assertions for Java controller methods and FastAPI routes.
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
        "security_tags",
        "http_method",
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


def test_java_controller_methods_chunked_individually():
    from app.services.parser import chunk_source

    code = """@RestController
public class ProductController {
    @GetMapping("/products")
    @PreAuthorize("hasRole('STAFF_MEMBER')")
    public List<Product> getProducts() { return service.getAll(); }

    @PostMapping("/products")
    @PreAuthorize("hasAnyRole('ASSISTANT_MANAGER','MANAGER','ADMIN')")
    public Product addProduct(@RequestBody Product p) { return service.add(p); }

    @DeleteMapping("/products/{id}")
    @PreAuthorize("hasAnyRole('MANAGER','ADMIN')")
    public void deleteProduct(@PathVariable Long id) { service.delete(id); }
}
"""

    chunks = [chunk.as_dict() for chunk in chunk_source("ProductController.java", "java", code) if chunk.chunk_type == "method"]

    assert len(chunks) == 3
    assert chunks[0]["http_method"] == "GET"
    assert chunks[1]["http_method"] == "POST"
    assert chunks[2]["http_method"] == "DELETE"
    assert "staff_member" in chunks[0]["security_tags"]
    assert "manager" in chunks[1]["security_tags"]
    assert "admin" in chunks[2]["security_tags"]


def test_python_fastapi_endpoint_chunked():
    from app.services.parser import chunk_source

    code = """@router.get("/items", dependencies=[Depends(get_current_user)])
async def get_items(current_user: User = Depends(get_current_user)):
    return []
"""

    chunks = [chunk.as_dict() for chunk in chunk_source("routes.py", "python", code) if chunk.symbol == "get_items"]

    assert chunks
    assert chunks[0]["http_method"] == "GET"
    assert any(tag in chunks[0]["security_tags"] for tag in ["login_required", "get_current_user", "authenticated", "potential_helper"])
