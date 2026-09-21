package edu.f21.common;
public class BusinessException extends RuntimeException {
    public final int status;
    public final String code;
    public BusinessException(int status,String code,String message) {
        super(message); this.status=status; this.code=code;
    }
    public static BusinessException forbidden() { return new BusinessException(403,"FORBIDDEN","无权访问此课程或用户数据"); }
    public static BusinessException missing() { return new BusinessException(404,"NOT_FOUND","内容不存在或已下架"); }
}
