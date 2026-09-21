package edu.f21;
import java.util.List;
/** Smoothed weighted accuracy of at most the latest 20 applied observations. Not BKT mastery. */
public final class RuleBaseline {
 private RuleBaseline(){}
 public record Observation(boolean correct,double weight){}
 public static double estimate(List<Observation> observations){
  if(observations.size()>20)throw new IllegalArgumentException("At most 20 observations");
  double correct=0,total=0;
  for(var o:observations){
   if(!Double.isFinite(o.weight())||o.weight()<=0||o.weight()>1)throw new IllegalArgumentException("Invalid weight");
   total+=o.weight();if(o.correct())correct+=o.weight();
  }
  return (1+correct)/(2+total);
 }
}
