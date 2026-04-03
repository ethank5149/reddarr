import {useEffect,useState,useRef} from "react"
import axios from "axios"

export default function App(){
  const [posts,setPosts]=useState([])
  const [offset,setOffset]=useState(0)
  const [search,setSearch]=useState("")
  const [searchResults,setSearchResults]=useState(null)
  const [selectedPost,setSelectedPost]=useState(null)
  const [tagInput,setTagInput]=useState("")
  const loader=useRef()
  const searchTimeout=useRef()

  useEffect(()=>{load()},[])

  function load(){
   axios.get(`/api/posts?limit=50&offset=${offset}`)
    .then(r=>{
      const newPosts = r.data.map(p => ({
        id: p[0],
        title: p[1],
        url: p[2],
        selftext: p[3],
        subreddit: p[4],
        author: p[5]
      }))
      setPosts(prev=>[...prev,...newPosts])
      setOffset(o=>o+50)
    })
  }

  useEffect(()=>{
   const obs=new IntersectionObserver(entries=>{
     if(entries[0].isIntersecting && !searchResults) load()
   })
   if(loader.current) obs.observe(loader.current)
  },[loader.current, searchResults])

  function handleSearch(e){
    setSearch(e.target.value)
    clearTimeout(searchTimeout.current)
    if(!e.target.value.trim()){
      setSearchResults(null)
      return
    }
    searchTimeout.current = setTimeout(()=>{
      axios.get(`/api/search?q=${encodeURIComponent(e.target.value)}`)
        .then(r=>{
          setSearchResults(r.data.map(p=>({id:p[0], title:p[1]})))
        })
    },300)
  }

  function addTag(){
    if(!tagInput.trim() || !selectedPost) return
    axios.post(`/api/tag?post_id=${selectedPost.id}&tag=${encodeURIComponent(tagInput)}`)
      .then(()=>{
        setSelectedPost({...selectedPost, tags:[...(selectedPost.tags||[]),tagInput]})
        setTagInput("")
      })
  }

  function formatDate(ts){
    if(!ts) return ""
    return new Date(ts * 1000).toLocaleDateString()
  }

  function truncateText(text, len=150){
    if(!text) return ""
    return text.length > len ? text.substring(0, len) + "..." : text
  }

  return (
    <div style={{minHeight:"100vh",background:"#1a1a1a",color:"#fff",fontFamily:"system-ui, sans-serif"}}>
      <header style={{padding:"15px 20px",background:"#2a2a2a",borderBottom:"1px solid #333",position:"sticky",top:0,zIndex:100}}>
        <div style={{display:"flex",alignItems:"center",justifyContent:"space-between",maxWidth:"1400px",margin:"0 auto"}}>
          <h1 style={{margin:0,fontSize:"24px",color:"#ff4500"}}>Reddit Archive</h1>
          <input 
            type="text" 
            placeholder="Search posts..." 
            value={search}
            onChange={handleSearch}
            style={{
              padding:"10px 15px",
              borderRadius:"20px",
              border:"none",
              width:"300px",
              background:"#3a3a3a",
              color:"#fff"
            }}
          />
        </div>
      </header>

      {searchResults && (
        <div style={{padding:"20px",maxWidth:"1400px",margin:"0 auto"}}>
          <div style={{display:"flex",justifyContent:"space-between",alignItems:"center",marginBottom:"20px"}}>
            <h2>Search Results ({searchResults.length})</h2>
            <button onClick={()=>{setSearchResults(null);setSearch("")}} style={{padding:"8px 16px",background:"#ff4500",border:"none",borderRadius:"5px",color:"#fff",cursor:"pointer"}}>Clear</button>
          </div>
          <div style={{display:"grid",gridTemplateColumns:"repeat(auto-fill,280px)",gap:"15px"}}>
            {searchResults.map(p=>(
              <div key={p.id} onClick={()=>setSelectedPost(p)} style={{background:"#2a2a2a",padding:"15px",borderRadius:"8px",cursor:"pointer",border:"1px solid #333"}}>
                <div style={{fontWeight:"500",marginBottom:"8px"}}>{p.title}</div>
              </div>
            ))}
          </div>
        </div>
      )}

      {!searchResults && (
        <div style={{padding:"20px",maxWidth:"1400px",margin:"0 auto"}}>
          <div style={{display:"grid",gridTemplateColumns:"repeat(auto-fill,300px)",gap:"20px"}}>
            {posts.map(p=>(
              <div key={p.id} onClick={()=>setSelectedPost(p)} style={{background:"#2a2a2a",borderRadius:"12px",overflow:"hidden",cursor:"pointer",transition:"transform 0.2s, box-shadow 0.2s",border:"1px solid #333"}}>
                {p.url ? (
                  <div style={{aspectRatio:"1",background:"#3a3a3a"}}>
                    <img src={p.url} style={{width:"100%",height:"100%",objectFit:"cover"}} onError={e=>e.target.style.display="none"}/>
                  </div>
                ) : (
                  <div style={{padding:"20px",background:"linear-gradient(135deg, #2a2a2a 0%, #333 100%)"}}>
                    <div style={{fontSize:"12px",color:"#888",marginBottom:"10px",textTransform:"uppercase",letterSpacing:"1px"}}>
                      {p.subreddit || "reddit"}
                    </div>
                    <div style={{fontSize:"16px",fontWeight:"600",marginBottom:"12px",lineHeight:"1.4"}}>{p.title}</div>
                    {p.selftext && (
                      <div style={{fontSize:"14px",color:"#aaa",lineHeight:"1.6"}}>
                        {truncateText(p.selftext)}
                      </div>
                    )}
                  </div>
                )}
                {p.url && (
                  <div style={{padding:"12px"}}>
                    <div style={{fontSize:"12px",color:"#888",marginBottom:"5px",textTransform:"uppercase",letterSpacing:"1px"}}>
                      {p.subreddit || "reddit"}
                    </div>
                    <div style={{fontSize:"14px",overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}}>{p.title}</div>
                  </div>
                )}
              </div>
            ))}
          </div>
          <div ref={loader} style={{padding:"40px",textAlign:"center",color:"#666"}}>Loading more...</div>
        </div>
      )}

      {selectedPost && (
        <div style={{position:"fixed",inset:0,background:"rgba(0,0,0,0.85)",display:"flex",alignItems:"center",justifyContent:"center",zIndex:200,padding:"20px"}} onClick={()=>setSelectedPost(null)}>
          <div style={{background:"#1a1a1a",borderRadius:"16px",maxWidth:"700px",width:"100%",maxHeight:"85vh",overflow:"auto",border:"1px solid #333"}} onClick={e=>e.stopPropagation()}>
            {selectedPost.url && (
              <div style={{background:"#000"}}>
                <img src={selectedPost.url} style={{width:"100%",maxHeight:"400px",objectFit:"contain"}} onError={e=>e.target.style.display="none"}/>
              </div>
            )}
            <div style={{padding:"25px"}}>
              <div style={{display:"flex",gap:"15px",fontSize:"13px",color:"#888",marginBottom:"15px"}}>
                <span style={{color:"#ff4500"}}>r/{selectedPost.subreddit || "reddit"}</span>
                <span>u/{selectedPost.author || "unknown"}</span>
              </div>
              <h2 style={{margin:"0 0 20px 0",fontSize:"22px",lineHeight:"1.4"}}>{selectedPost.title}</h2>
              {selectedPost.selftext && (
                <div style={{background:"#252525",padding:"20px",borderRadius:"10px",marginBottom:"20px",fontSize:"15px",lineHeight:"1.7",color:"#ccc",whiteSpace:"pre-wrap"}}>
                  {selectedPost.selftext}
                </div>
              )}
              <div style={{display:"flex",gap:"10px"}}>
                <input 
                  type="text" 
                  placeholder="Add tag..." 
                  value={tagInput}
                  onChange={e=>setTagInput(e.target.value)}
                  onKeyDown={e=>e.key==="Enter"&&addTag()}
                  style={{flex:1,padding:"12px",borderRadius:"8px",border:"1px solid #333",background:"#252525",color:"#fff",fontSize:"14px"}}
                />
                <button onClick={addTag} style={{padding:"12px 24px",background:"#ff4500",border:"none",borderRadius:"8px",color:"#fff",cursor:"pointer",fontWeight:"600"}}>Add Tag</button>
              </div>
              <div style={{marginTop:"20px",color:"#555",fontSize:"12px"}}>Post ID: {selectedPost.id}</div>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}