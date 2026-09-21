package edu.f21;
import static org.junit.jupiter.api.Assertions.*;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;
public class BktTest {
 @Test void rejectsNonFiniteValues(){
  assertThrows(IllegalArgumentException.class,()->Bkt.update(Double.NaN,true,1));
  assertThrows(IllegalArgumentException.class,()->Bkt.update(.2,true,Double.NaN));
  assertThrows(IllegalArgumentException.class,()->Bkt.update(.2,true,Double.POSITIVE_INFINITY));
 }
 @Test void clipsExtremeProbabilities(){assertEquals(1-1e-6,Bkt.update(1,true,1),1e-12);}
 @Test void sameReferenceVectorsAsPython() throws Exception {
  var stream=getClass().getResourceAsStream("/bkt-vectors.json");
  assertNotNull(stream);
  var vectors=new ObjectMapper().readTree(stream);
  for(var v:vectors) assertEquals(v.get("expected").asDouble(),
   Bkt.update(v.get("before").asDouble(),v.get("correct").asBoolean(),v.get("weight").asDouble()),1e-10);
 }
}
