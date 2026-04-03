import {useEffect,useState,useRef} from "react"
import axios from "axios"

export default function App(){
 const [posts,setPosts]=useState([])
 const [offset,setOffset]=useState(0)
 const loader=useRef()

 useEffect(()=>{load()},[])

 function load(){
  axios.get(`/api/posts?limit=50&offset=${offset}`)
   .then(r=>{
     setPosts(prev=>[...prev,...r.data])
     setOffset(o=>o+50)
   })
 }

 useEffect(()=>{
  const obs=new IntersectionObserver(entries=>{
    if(entries[0].isIntersecting) load()
  })
  if(loader.current) obs.observe(loader.current)
 },[loader.current])

 return (
  <div>
    <div style={{display:"grid",gridTemplateColumns:"repeat(auto-fill,250px)",gap:"10px"}}>
      {posts.map(p=>(
        <div key={p[0]}>
          <img src={p[2]} style={{width:"100%"}}/>
          <div>{p[1]}</div>
        </div>
      ))}
    </div>
    <div ref={loader}>Loading...</div>
  </div>
 )
}
