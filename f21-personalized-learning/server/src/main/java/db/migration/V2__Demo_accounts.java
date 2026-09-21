package db.migration;
import org.flywaydb.core.api.migration.BaseJavaMigration;
import org.flywaydb.core.api.migration.Context;
import org.springframework.security.crypto.bcrypt.BCryptPasswordEncoder;
public class V2__Demo_accounts extends BaseJavaMigration {
 @Override public Integer getChecksum(){return 1;}
 @Override public void migrate(Context context) throws Exception {
  String[][] users={
   {"3d7a8826-cee4-525a-98d7-8cdb937e677e","student01","林同学","STUDENT"},
   {"20000000-0000-4000-8000-000000000002","student02","边界测试学生","STUDENT"},
   {"20000000-0000-4000-8000-000000000003","teacher01","陈老师","TEACHER"},
   {"20000000-0000-4000-8000-000000000004","teacher02","边界测试教师","TEACHER"},
   {"20000000-0000-4000-8000-000000000005","admin01","演示管理员","ADMIN"}
  };
  var encoder=new BCryptPasswordEncoder(12);
  try(var stmt=context.getConnection().prepareStatement(
      "INSERT INTO local_user(user_id,username,display_name,role,password_hash) VALUES (?,?,?,?,?)")){
   for(String[] u:users){
    for(int i=0;i<4;i++)stmt.setString(i+1,u[i]);
    stmt.setString(5,encoder.encode("Learn@12345"));stmt.executeUpdate();
   }
  }
 }
}
