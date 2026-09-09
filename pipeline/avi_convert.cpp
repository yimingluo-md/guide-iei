// Streaming, lossless AVI TSV -> per-chromosome grouped-ALT BGZF VCF.
// Input is a BGZF member inside a ZIP_STORED archive, at a verified offset.
// zlib validates every decoded BGZF block CRC. No patient data is involved.
#include <zlib.h>
#include <fcntl.h>
#include <unistd.h>
#include <algorithm>
#include <array>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <stdexcept>
#include <string>

static void require(bool ok, const std::string& message) {
  if (!ok) throw std::runtime_error(message);
}
class Bgzf {
  FILE* out;
  std::string buffer;
 public:
  explicit Bgzf(const char* path) : out(std::fopen(path, "wb")) {
    require(out != nullptr, "cannot create output"); buffer.reserve(131072);
  }
  ~Bgzf() { if (out) std::fclose(out); }
  void block(const char* data, size_t size) {
    std::array<unsigned char, 65536> encoded{};
    unsigned char header[] = {31,139,8,4,0,0,0,0,0,255,6,0,66,67,2,0,0,0};
    std::memcpy(encoded.data(), header, 18);
    z_stream stream{};
    require(deflateInit2(&stream, 6, Z_DEFLATED, -15, 8, Z_DEFAULT_STRATEGY) == Z_OK, "deflate initialization failed");
    stream.next_in = reinterpret_cast<Bytef*>(const_cast<char*>(data));
    stream.avail_in = static_cast<uInt>(size);
    stream.next_out = encoded.data() + 18; stream.avail_out = 65510;
    int result = deflate(&stream, Z_FINISH);
    size_t bytes = stream.total_out; deflateEnd(&stream);
    require(result == Z_STREAM_END, "BGZF compression failed");
    size_t length = bytes + 26;
    encoded[16] = (length-1)&255; encoded[17] = (length-1)>>8;
    uint32_t crc = crc32(0, reinterpret_cast<const Bytef*>(data), size);
    for (int i=0; i<4; ++i) {
      encoded[18+bytes+i] = (crc>>(8*i))&255;
      encoded[22+bytes+i] = (size>>(8*i))&255;
    }
    require(std::fwrite(encoded.data(), 1, length, out) == length, "output write failed (check free disk space)");
  }
  void write(const std::string& line) {
    buffer += line;
    while (buffer.size() >= 65280) {
      block(buffer.data(), 65280); buffer.erase(0, 65280);
    }
  }
  void close() {
    if (!buffer.empty()) block(buffer.data(), buffer.size());
    block("", 0);
    require(std::fflush(out) == 0, "output flush failed");
    FILE* handle=out; out=nullptr;
    require(std::fclose(handle)==0, "output close failed");
  }
};
static double score(const std::string& token) {
  // Strict decimal grammar; strtod alone accepts hex floats and whitespace.
  size_t i=0; if (i<token.size() && (token[i]=='+' || token[i]=='-')) ++i;
  size_t digits=0;
  while (i<token.size() && token[i]>='0' && token[i]<='9') { ++i; ++digits; }
  if (i<token.size() && token[i]=='.') {
    ++i;
    while (i<token.size() && token[i]>='0' && token[i]<='9') { ++i; ++digits; }
  }
  require(digits>0, "invalid score");
  if (i<token.size() && (token[i]=='e' || token[i]=='E')) {
    ++i; if (i<token.size() && (token[i]=='+' || token[i]=='-')) ++i;
    size_t start=i;
    while (i<token.size() && token[i]>='0' && token[i]<='9') ++i;
    require(i>start, "invalid score exponent");
  }
  require(i==token.size(), "invalid score token");
  double value=std::strtod(token.c_str(), nullptr);
  require(std::isfinite(value), "non-finite score");
  return value;
}
int main(int argc, char** argv) {
  gzFile input=nullptr;
  try {
    require(argc==9, "usage: avi_convert ARCHIVE COMPRESSED_OFFSET SKIP CHROM EXPECTED_ROWS OUTPUT STATS PROGRESS");
    int fd=open(argv[1], O_RDONLY); require(fd>=0, "cannot open source archive");
    require(lseek(fd, std::stoll(argv[2]), SEEK_SET)>=0, "cannot seek source archive");
    input=gzdopen(fd,"rb"); require(input!=nullptr,"cannot open BGZF stream");
    gzbuffer(input, 1024*1024);
    size_t skip=std::stoull(argv[3]); std::array<char,65536> skipped{};
    require(skip<65536,"invalid index virtual offset");
    require(gzread(input,skipped.data(),skip)==static_cast<int>(skip),"invalid index virtual offset");
    const std::string chrom=argv[4]; const uint64_t expected=std::stoull(argv[5]);
    Bgzf output(argv[6]);
    uint64_t count=0, positions=0, pos=0; char ref=0;
    std::array<std::string,4> raw{}, phred{};
    auto flush = [&]() {
      if (!pos) return;
      std::string alts,raws,phreds;
      for (int i=0; i<4; ++i) {
        char base="ACGT"[i]; if (base==ref) continue;
        require(!raw[i].empty(),"position does not have all three distinct ALT scores");
        if (!alts.empty()) { alts+=','; raws+=','; phreds+=','; }
        alts+=base; raws+=raw[i]; phreds+=phred[i];
      }
      output.write(chrom.substr(3)+"\t"+std::to_string(pos)+"\t.\t"+ref+"\t"+alts+"\t.\t.\traw="+raws+";phred="+phreds+"\n");
      ++positions;
    };
    std::array<char,2048> line{};
    while (count<expected) {
      require(gzgets(input,line.data(),line.size())!=nullptr,"source ended before indexed row count");
      size_t n=std::strlen(line.data()); require(n && line[n-1]=='\n',"oversized or unterminated TSV row");
      line[--n]=0; if(n && line[n-1]=='\r') line[--n]=0;
      std::array<std::string,6> field{}; size_t start=0,j=0;
      for (size_t i=0;i<=n;++i) if(i==n || line[i]=='\t') {
        require(j<6,"extra TSV column"); field[j++]=std::string(line.data()+start,i-start); start=i+1;
      }
      require(j==6 && field[0]==chrom,"unexpected TSV schema or chromosome/index mismatch");
      require(!field[1].empty() && field[1].find_first_not_of("0123456789")==std::string::npos,"invalid position");
      uint64_t next=std::stoull(field[1]); require(next>0 && next<=536870911,"position outside TBI coordinates");
      require(field[2].size()==1 && field[3].size()==1,"non-SNV allele");
      const char* bases="ACGT";
      const char* ri=std::strchr(bases,field[2][0]); const char* ai=std::strchr(bases,field[3][0]);
      require(ri && ai && ri!=ai,"invalid REF/ALT");
      score(field[4]); require(score(field[5])>=0,"negative Phred score");
      if(next!=pos) { require(next>pos,"unsorted or repeated position"); flush(); pos=next; ref=field[2][0]; raw={}; phred={}; }
      require(ref==field[2][0],"conflicting reference at position");
      size_t a=ai-bases;
      require(raw[a].empty(),"duplicate ALT at position"); raw[a]=field[4]; phred[a]=field[5];
      ++count;
      if(count%1000000==0) { std::ofstream progress(argv[8]); progress<<count; }
    }
    flush();
    // There must not be another row of this chromosome beyond the count.
    if (gzgets(input,line.data(),line.size()))
      require(std::strncmp(line.data(),(chrom+"\t").c_str(),chrom.size()+1)!=0,"index row count is too small");
    int err=0; gzerror(input,&err); require(err==Z_OK || err==Z_STREAM_END,"corrupt BGZF stream");
    gzclose(input); input=nullptr; output.close();
    require(count==positions*3,"incomplete alternate groups");
    std::ofstream stats(argv[7]); stats<<"{\"rows\":"<<count<<",\"positions\":"<<positions<<"}\n";
    require(stats.good(),"cannot write statistics");
    return 0;
  } catch(const std::exception& error) {
    if(input) gzclose(input);
    std::fprintf(stderr,"AVI conversion failed: %s\n",error.what()); return 1;
  }
}
