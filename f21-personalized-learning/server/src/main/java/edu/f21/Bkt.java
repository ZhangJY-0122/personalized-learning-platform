package edu.f21;
public final class Bkt {
 private Bkt() {}
 public static double update(double l, boolean correct, double weight) {
  if (!Double.isFinite(l) || !Double.isFinite(weight) || l<0 || l>1 || weight<=0 || weight>1) throw new IllegalArgumentException("invalid probability");
  double p=l*.9+(1-l)*.2;
  double q=correct ? l*.9/p : l*.1/(1-p);
  return Math.max(1e-6,Math.min(1-1e-6,l+weight*(q+(1-q)*.1-l)));
 }
}
