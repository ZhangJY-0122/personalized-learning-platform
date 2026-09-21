package edu.f21.common;
import jakarta.servlet.*;
import jakarta.servlet.http.*;
import java.io.IOException;
import java.util.UUID;
import org.springframework.core.Ordered;
import org.springframework.core.annotation.Order;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;

@Component
@Order(Ordered.HIGHEST_PRECEDENCE)
public class TraceFilter extends OncePerRequestFilter {
 @Override protected void doFilterInternal(HttpServletRequest request,HttpServletResponse response,FilterChain chain)
 throws ServletException,IOException {
  String id=UUID.randomUUID().toString();
  request.setAttribute("traceId",id);
  response.setHeader("X-Trace-Id",id);
  response.setHeader("Cache-Control","no-store");
  chain.doFilter(request,response);
 }
}
