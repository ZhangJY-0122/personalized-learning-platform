package edu.f21.common;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.bind.MethodArgumentNotValidException;
import org.springframework.http.converter.HttpMessageNotReadableException;
import org.springframework.web.method.annotation.MethodArgumentTypeMismatchException;
import org.springframework.web.servlet.resource.NoResourceFoundException;
import org.springframework.web.bind.MissingServletRequestParameterException;
import org.springframework.web.bind.MissingRequestHeaderException;
import org.springframework.web.HttpRequestMethodNotSupportedException;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

@RestControllerAdvice
public class Errors {
 private static final Logger log=LoggerFactory.getLogger(Errors.class);
 @ExceptionHandler(BusinessException.class)
 ResponseEntity<?> business(BusinessException ex) {
  return ResponseEntity.status(ex.status).body(Api.error(ex.code,ex.getMessage(),Api.trace()));
 }
 @ExceptionHandler({MethodArgumentNotValidException.class,HttpMessageNotReadableException.class,
     MethodArgumentTypeMismatchException.class,MissingServletRequestParameterException.class,MissingRequestHeaderException.class})
 ResponseEntity<?> invalid(Exception ex) {
  return ResponseEntity.badRequest().body(Api.error("INVALID_ARGUMENT","请求字段缺失或格式不正确",Api.trace()));
 }
 @ExceptionHandler(NoResourceFoundException.class)
 ResponseEntity<?> notFound(Exception ex) {
  return ResponseEntity.status(404).body(Api.error("NOT_FOUND","接口不存在",Api.trace()));
 }
 @ExceptionHandler(HttpRequestMethodNotSupportedException.class)
 ResponseEntity<?> method(Exception ex) {
  return ResponseEntity.status(405).body(Api.error("INVALID_ARGUMENT","不支持此请求方法",Api.trace()));
 }
 @ExceptionHandler(Exception.class)
 ResponseEntity<?> unknown(Exception ex) {
  log.error("request failed traceId={} type={} message={}",Api.trace(),ex.getClass().getSimpleName(),ex.getMessage(),ex);
  return ResponseEntity.status(500).body(Api.error("INTERNAL_ERROR","服务暂时不可用，请稍后重试",Api.trace()));
 }
}
