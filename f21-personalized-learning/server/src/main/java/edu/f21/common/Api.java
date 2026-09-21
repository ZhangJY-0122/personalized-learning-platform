package edu.f21.common;

import java.util.Map;
import java.util.UUID;
import org.springframework.web.context.request.RequestContextHolder;
import org.springframework.web.context.request.ServletRequestAttributes;

public final class Api {
    private Api() {}
    public static String trace() {
        var attrs = RequestContextHolder.getRequestAttributes();
        if (attrs instanceof ServletRequestAttributes a) {
            Object id = a.getRequest().getAttribute("traceId");
            if (id != null) return id.toString();
        }
        return UUID.randomUUID().toString();
    }
    public static Map<String,Object> ok(Object data) {
        return Map.of("code","OK","message","success","data",data,"traceId",trace());
    }
    public static Map<String,Object> error(String code,String message,String traceId) {
        return Map.of("code",code,"message",message,"traceId",traceId);
    }
}
