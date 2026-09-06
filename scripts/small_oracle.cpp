// Independent test oracle: exact __int128 rational numbers and encoding search.
// No production arithmetic or generated RTL is linked into this program.
#include <cstdint>
#include <iostream>
#include <vector>
#include <string>
#include <cstdlib>
#include <algorithm>
using U = unsigned __int128;
using I = __int128;
struct D { bool sign; uint64_t v; int kind; bool snan; };
struct Format {
  int w,e,f,bias,kind;
  uint32_t nan,inf;
  uint64_t scale;
  std::vector<uint64_t> values;
  D decode(uint32_t b) const {
    bool s=b>>(w-1); uint32_t ef=(b>>f)&((1<<e)-1), m=b&((1<<f)-1);
    if(ef==uint32_t((1<<e)-1)) {
      if(kind==0) return {s,0,m?2:1,m && !(m&(1<<(f-1)))};
      if(kind==1 && m==uint32_t((1<<f)-1)) return {s,0,2,false};
    }
    return {s,ef?uint64_t(m+(1<<f))<<(ef-1):m,0,false};
  }
};
bool increment(U q,U r,U d,int rm,bool sign) {
  if(!r) return false;
  if(rm==0) return r*2>d || (r*2==d && (q&1));
  if(rm==4) return r*2>=d;
  return (rm==2 && sign) || (rm==3 && !sign);
}
int cmp(U a,U b) { return a>b?1:a<b?-1:0; }
int bitlength(U x) { int n=0; while(x) {++n; x>>=1;} return n; }
uint32_t compute(const Format &f,int op,uint32_t av,uint32_t bv,uint32_t cv,int rm) {
  auto a=f.decode(av),b=f.decode(bv),c=op==2?f.decode(cv):D{false,0,0,false};
  bool sign=a.sign^b.sign;
  bool az=!a.kind&&!a.v,bz=!b.kind&&!b.v;
  bool invalid=a.snan||b.snan||c.snan;
  bool nan=a.kind==2||b.kind==2||c.kind==2;
  if(op==1||op==2) invalid|=(a.kind==1&&bz)||(b.kind==1&&az);
  auto pack=[&](uint32_t bits,int flags,bool s){return (uint32_t(flags)<<8)|bits|(uint32_t(s)<<(f.w-1));};
  if(nan) return pack(f.nan,invalid?16:0,false);
  if(op==0) invalid|=a.kind==1&&b.kind==1&&a.sign!=b.sign;
  if(op==2) invalid|=(a.kind==1||b.kind==1)&&c.kind==1&&sign!=c.sign;
  if(op==3) invalid|=(az&&bz)||(a.kind==1&&b.kind==1);
  if(invalid) return pack(f.nan,16,false);
  if(op==0&&(a.kind==1||b.kind==1)) return pack(f.inf,0,a.kind==1?a.sign:b.sign);
  if((op==1||op==2)&&(a.kind==1||b.kind==1)) return pack(f.inf,0,sign);
  if(op==2&&c.kind==1) return pack(f.inf,0,c.sign);
  if(op==3) {
    if(a.kind==1) return pack(f.inf,0,sign);
    if(b.kind==1) return pack(0,0,sign);
    if(bz) return pack(f.kind==0?f.inf:f.values.size()-1,8,sign);
  }
  U n=0,d=1;
  if(op==0||op==2) {
    U x=op==0?a.v:U(a.v)*b.v, y=op==0?b.v:U(c.v)*f.scale;
    bool sx=op==0?a.sign:sign,sy=op==0?b.sign:c.sign;
    I v=(sx?-I(x):I(x))+(sy?-I(y):I(y));
    sign=v<0; n=v<0?U(-v):U(v);
    if(!v) sign=(!x&&!y&&sx==sy)?sx:rm==2;
    d=op==0?1:f.scale;
  } else if(op==1) { n=U(a.v)*b.v; d=f.scale; }
  else {n=U(a.v)*f.scale;d=b.v;}
  if(!n) return pack(0,0,sign);
  int top=bitlength(n)-bitlength(d);
  if((top>=0?n<d<<top:n<<-top<d)) --top;
  int quantum=top-f.f;
  U nn=quantum<0?n<<-quantum:n, dd=quantum>=0?d<<quantum:d;
  U q=nn/dd,r=nn%dd; q+=increment(q,r,dd,rm,sign);
  auto compareRounded=[&](uint64_t v){return quantum>=0?cmp(q<<quantum,v):cmp(q,U(v)<<-quantum);};
  bool overflow=f.kind? n>U(f.values.back())*d : compareRounded(f.values.back())>0;
  if(overflow) {
    bool toinf=f.kind==0&&(rm==0||rm==4||(rm==2&&sign)||(rm==3&&!sign));
    return pack(toinf?f.inf:f.values.size()-1,5,sign);
  }
  int lo=0,hi=f.values.size();
  while(lo<hi) {int mid=(lo+hi)/2;if(U(f.values[mid])*d<n)lo=mid+1;else hi=mid;}
  if(lo<int(f.values.size())&&U(f.values[lo])*d==n) return pack(lo,0,sign);
  int result;
  if(lo==int(f.values.size())) result=lo-1;
  else {
    U remainder=n-U(f.values[lo-1])*d,step=U(f.values[lo]-f.values[lo-1])*d;
    result=lo-1+increment(lo-1,remainder,step,rm,sign);
  }
  bool tiny=compareRounded(1<<f.f)<0;
  return pack(result,tiny?3:1,sign);
}
int main(int argc,char**argv) {
  if(argc!=6)return 2;
  std::string name=argv[1];int op=std::atoi(argv[2]);uint64_t begin=std::strtoull(argv[3],nullptr,10),end=std::strtoull(argv[4],nullptr,10);int rm=std::atoi(argv[5]);
  Format f=name=="e2m1"?Format{4,2,1,1,2}:name=="e4m3fn"?Format{8,4,3,7,1}:Format{8,5,2,15,0};
  f.inf=((1<<f.e)-1)<<f.f;f.nan=f.kind==2?0:f.kind==1?(1<<(f.w-1))-1:f.inf|(1<<(f.f-1));
  f.scale=uint64_t(1)<<(f.bias+f.f-1);
  for(int i=0;i<(1<<(f.w-1));++i) {auto d=f.decode(i);if(d.kind==0)f.values.push_back(d.v);}
  uint64_t mask=(1<<f.w)-1;
  std::vector<uint16_t> out;out.reserve(end-begin);
  for(uint64_t i=begin;i<end;++i) {
    uint32_t c=op==2?i&mask:0,b=(op==2?i>>f.w:i)&mask,a=(op==2?i>>(2*f.w):i>>f.w)&mask;
    out.push_back(compute(f,op,a,b,c,rm));
  }
  std::cout.write(reinterpret_cast<const char*>(out.data()),out.size()*2);
}
