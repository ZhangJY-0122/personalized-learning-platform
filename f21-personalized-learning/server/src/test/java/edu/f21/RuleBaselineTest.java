package edu.f21;
import static org.junit.jupiter.api.Assertions.*;
import java.util.List;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;
public class RuleBaselineTest {
 @Test void noEvidenceIsHalf(){assertEquals(.5,RuleBaseline.estimate(List.of()));}
 @Test void oneCorrectAnswerIsNotCertainty(){assertEquals(2.0/3,RuleBaseline.estimate(List.of(new RuleBaseline.Observation(true,1))),1e-12);}
 @Test void weightedAccuracyIsSmoothed(){assertEquals(1.5/3,RuleBaseline.estimate(List.of(new RuleBaseline.Observation(true,.5),new RuleBaseline.Observation(false,.5))),1e-12);}
 @Test void badWeightRejected(){assertThrows(IllegalArgumentException.class,()->RuleBaseline.estimate(List.of(new RuleBaseline.Observation(true,Double.NaN))));}
 @Test void sameReferenceVectorsAsPython() throws Exception {
  var stream=getClass().getResourceAsStream("/rule-vectors.json");
  assertNotNull(stream);
  var vectors=new ObjectMapper().readTree(stream);
  for(var vector:vectors){
   var history=new java.util.ArrayList<RuleBaseline.Observation>();
   for(var answer:vector.get("history")) history.add(new RuleBaseline.Observation(answer.asBoolean(),1));
   if(history.size()>20) history=new java.util.ArrayList<>(history.subList(history.size()-20,history.size()));
   assertEquals(vector.get("expected").asDouble(),RuleBaseline.estimate(history),1e-12);
  }
 }
}
