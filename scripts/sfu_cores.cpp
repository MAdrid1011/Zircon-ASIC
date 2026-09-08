// Exhaust normalized significands independently of the floating-point wrapper.
#include <algorithm>
#include <cstdint>
#include <fstream>
#include <iostream>
#include <vector>
#include <utility>
using U=unsigned __int128;

uint64_t root(uint64_t x) {
  uint64_t r=0,bit=uint64_t(1)<<62;
  while(bit>x)bit>>=2;
  while(bit) {
    if(x>=r+bit) {x-=r+bit;r=(r>>1)+bit;} else r>>=1;
    bit>>=2;
  }
  return r;
}

std::pair<uint64_t,uint64_t> nonrestoring(uint64_t x,unsigned bits) {
  int64_t remainder=0;uint64_t q=0;
  for(int i=bits-1;i>=0;--i) {
    int64_t pair=(x>>(i*2))&3;
    remainder = remainder>=0 ? remainder*4+pair-int64_t((q<<2)|1) : remainder*4+pair+int64_t((q<<2)|3);
    q=(q<<1)|(remainder>=0);
  }
  if(remainder<0)remainder+=int64_t((q<<1)|1);
  return {q,uint64_t(remainder)};
}

std::pair<uint64_t,uint64_t> reciprocal_digits(uint64_t m,unsigned fb,unsigned bits) {
  uint64_t remainder=uint64_t(1)<<fb,q=0;
  for(unsigned i=0;i<bits;++i) {
    bool ge=remainder>=m;
    if(ge)remainder-=m;
    q=(q<<1)|ge;remainder<<=1;
  }
  return {q,remainder>>1};
}

int main(int argc,char **argv) {
  if(argc<2 || argc>3)return 2;
  std::ifstream file(argv[1],std::ios::binary);
  uint64_t f,b,iterations,fb,algorithm,mask;file.read((char*)&f,8);file.read((char*)&b,8);file.read((char*)&iterations,8);file.read((char*)&fb,8);
  file.read((char*)&algorithm,8);file.read((char*)&mask,8);
  std::vector<uint64_t> reciprocal(1<<b),rsqrt(2<<b);
  file.read((char*)reciprocal.data(),reciprocal.size()*8);file.read((char*)rsqrt.data(),rsqrt.size()*8);
  if(!file)return 2;
  std::ofstream vectors;
  if(argc==3) {vectors.open(argv[2],std::ios::binary);if(fb!=23 || !vectors)return 2;}
  uint64_t vectorCount=0;
  auto emit=[&](uint32_t input,uint64_t value,bool sticky) {
    if(argc!=3)return;
    unsigned top=63-__builtin_clzll(value),cut=top-fb;
    auto lower=value&((uint64_t(1)<<cut)-1),half=uint64_t(1)<<(cut-1);
    uint64_t mantissa=value>>cut;
    mantissa+=(lower>half || (lower==half && (sticky || (mantissa&1))));
    int exponent=int(top)-int(fb+4)+127;
    if(mantissa==(uint64_t(1)<<(fb+1))) {mantissa>>=1;++exponent;}
    uint32_t row[4]={input,uint32_t((uint32_t(exponent)<<fb)|(mantissa&((uint64_t(1)<<fb)-1))),uint32_t(lower!=0 || sticky),0};
    vectors.write((char*)row,sizeof(row));++vectorCount;
  };
  uint64_t qbits=fb+4,checked=0,maxrcp=0,maxrsqrt=0,maxsqrt=0;
  U target=U(1)<<(qbits+fb),squareTarget=U(1)<<(2*qbits+fb);
  for(uint64_t m=uint64_t(1)<<fb;m<(uint64_t(1)<<(fb+1));++m) {
    uint64_t index=(m-(uint64_t(1)<<fb))>>(fb-b);
    uint64_t y=reciprocal[index],d=U(m)*y>>fb;
    if(mask&1) {
      for(unsigned i=0;algorithm<2 && i<iterations;++i) {
        uint64_t my=algorithm?d:U(m)*y>>fb;
        auto factor=(uint64_t(2)<<f)-my;
        y=U(y)*factor>>f;
        if(algorithm)d=U(d)*factor>>f;
      }
      uint64_t candidate=algorithm==3?reciprocal_digits(m,fb,qbits+1).first:y>>(f-qbits),exact=target/m;
      if((target%m==0)!=(m==(uint64_t(1)<<fb)))return 1;
      if(algorithm==3 && reciprocal_digits(m,fb,qbits+1).second!=target%m)return 1;
      uint64_t error=candidate>exact?candidate-exact:exact-candidate;
      maxrcp=std::max(maxrcp,error);
      if(error>1) {std::cerr<<"reciprocal "<<m<<" "<<candidate<<" "<<exact<<"\n";return 1;}
      emit((127u<<fb)|(m- (uint64_t(1)<<fb)),exact,target%m!=0);
    }
    if(!(mask&6))continue;
    for(unsigned parity=0;parity<2;++parity) {
      uint64_t a=m<<parity;y=rsqrt[(parity<<b)+index];d=U(a)*y>>fb;
      for(unsigned i=0;algorithm<2 && i<iterations;++i) {
        uint64_t yy=U(y)*y>>f;
        uint64_t myy=algorithm?U(d)*y>>f:U(a)*yy>>fb;
        auto factor=(uint64_t(3)<<f)-myy;
        y=U(y)*factor>>(f+1);
        if(algorithm)d=U(d)*factor>>(f+1);
      }
      auto radicand=a<<(2*qbits-fb);
      if(mask&2) {
        auto candidate=y>>(f-qbits),exact=root(squareTarget/a);
        if((U(a)*exact*exact==squareTarget)!=(m==(uint64_t(1)<<fb) && parity==0))return 1;
        auto error=candidate>exact?candidate-exact:exact-candidate;
        maxrsqrt=std::max(maxrsqrt,error);
        if(error>1) {std::cerr<<"rsqrt "<<m<<" "<<parity<<" "<<candidate<<" "<<exact<<"\n";return 1;}
        emit(((127u+parity)<<fb)|(m-(uint64_t(1)<<fb)),exact,U(a)*exact*exact!=squareTarget);
      }
      if(mask&4) {
        auto candidate=algorithm==2?nonrestoring(radicand,qbits+1).first:(algorithm?d:uint64_t(U(a)*y>>fb))>>(f-qbits),exact=root(radicand);
        auto error=candidate>exact?candidate-exact:exact-candidate;
        maxsqrt=std::max(maxsqrt,error);
        if(error>1) {std::cerr<<"sqrt refinement "<<m<<" "<<parity<<" "<<candidate<<" "<<exact<<"\n";return 1;}
        emit(((127u+parity)<<fb)|(m-(uint64_t(1)<<fb)),exact,exact*exact!=radicand);
      }
      auto nr=nonrestoring(radicand,qbits+1);
      if(nr.first!=root(radicand) || nr.second!=radicand-nr.first*nr.first) {std::cerr<<"sqrt "<<m<<"\n";return 1;}
      ++checked;
    }
  }
  std::cout<<"{\"normalized_root_inputs\":"<<checked<<",\"normalized_rcp_inputs\":"<<((mask&1)?(uint64_t(1)<<fb):0)
           <<",\"max_rcp_correction\":"<<maxrcp<<",\"max_rsqrt_correction\":"<<maxrsqrt<<",\"max_sqrt_correction\":"<<maxsqrt<<",\"dyadic_exactness_checked\":true,\"normalized_vectors\":"<<vectorCount<<",\"discrepancies\":0}\n";
}
