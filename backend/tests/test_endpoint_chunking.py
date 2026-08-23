def test_java_delete_mapping_chunked_individually():
    code = """
    @RestController
    @RequestMapping("/products")
    public class ProductController {
        @DeleteMapping("/{id}")
        @PreAuthorize("hasAnyRole('MANAGER','ADMIN')")
        public void deleteProduct(@PathVariable Long id) {
            productService.delete(id);
        }
    }
    """
    from app.services.parser import chunk_source
    chunks = chunk_source("ProductController.java", "java", code)
    delete_chunks = [c for c in chunks if getattr(c, "http_method", None) == "DELETE"]
    assert len(delete_chunks) >= 1, "deleteProduct must produce a DELETE chunk"
    tags_str = " ".join(str(t) for t in getattr(delete_chunks[0], "security_tags", []))
    assert "manager" in tags_str.lower(), "MANAGER role must be in security_tags"
    assert "admin" in tags_str.lower(), "ADMIN role must be in security_tags"
