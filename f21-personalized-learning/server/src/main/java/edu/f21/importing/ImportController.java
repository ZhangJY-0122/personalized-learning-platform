package edu.f21.importing;

import com.fasterxml.jackson.databind.JsonNode;
import edu.f21.common.Api;
import java.util.UUID;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/api/v1/admin")
public class ImportController {
    private final EventImportService events; private final CatalogImportService catalogs;
    public ImportController(EventImportService events, CatalogImportService catalogs) { this.events=events; this.catalogs=catalogs; }
    @PostMapping(value="/catalog-imports", consumes="application/json")
    public Object catalog(@AuthenticationPrincipal Jwt jwt,@RequestHeader("Idempotency-Key") UUID key,@RequestBody JsonNode body){ return Api.ok(catalogs.importCatalog(jwt,key.toString(),body)); }
    @PostMapping(value="/event-imports", consumes="application/json")
    public Object events(@AuthenticationPrincipal Jwt jwt,@RequestHeader("Idempotency-Key") UUID key,@RequestBody JsonNode body){ return Api.ok(events.batch(jwt,key.toString(),body)); }
    @PostMapping(value="/event-imports", consumes="text/csv")
    public Object csv(@AuthenticationPrincipal Jwt jwt,@RequestHeader("Idempotency-Key") UUID key,@RequestBody String body){ return Api.ok(events.csv(jwt,key.toString(),body)); }
}
