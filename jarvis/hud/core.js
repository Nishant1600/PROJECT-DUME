/* The JARVIS core: a reactive energy sphere rendered with three.js (global THREE, r128).
 * Three layers — a displaced inner sphere, a fresnel glow shell, and an orbiting particle
 * halo with three rotating rings — all driven by state + live mic/voice level.
 * window.JarvisCore.setState(name) and .setLevel(0..1) are called by app.js.
 */
(function () {
  "use strict";
  const canvas = document.getElementById("scene");
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true });
  renderer.setClearColor(0x000000, 0);
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));

  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(42, 1, 0.1, 100);
  camera.position.set(0, 0, 6.2);

  const group = new THREE.Group();
  scene.add(group);

  // palette per state (r,g,b 0..1) — hot = the energetic second colour
  const PALETTE = {
    idle:      { base: [0.18, 0.45, 0.62], hot: [0.33, 0.78, 0.95] },
    listening: { base: [0.20, 0.64, 0.86], hot: [0.46, 0.90, 1.00] },
    thinking:  { base: [0.45, 0.33, 0.78], hot: [0.74, 0.58, 1.00] },
    speaking:  { base: [0.22, 0.70, 0.84], hot: [0.55, 0.93, 1.00] },
    acting:    { base: [0.78, 0.52, 0.18], hot: [1.00, 0.72, 0.30] },
    boot:      { base: [0.15, 0.30, 0.45], hot: [0.30, 0.60, 0.85] },
  };

  const uniforms = {
    uTime: { value: 0 },
    uLevel: { value: 0 },      // smoothed audio level
    uEnergy: { value: 0.2 },   // state-driven baseline motion
    uBase: { value: new THREE.Color().setRGB(0.15, 0.3, 0.45) },
    uHot: { value: new THREE.Color().setRGB(0.3, 0.6, 0.85) },
  };

  // ---- inner sphere: vertex noise displacement + fresnel-tinted surface
  const sphereGeo = new THREE.IcosahedronGeometry(1.5, 32);
  const sphereMat = new THREE.ShaderMaterial({
    uniforms,
    transparent: true,
    vertexShader: `
      uniform float uTime; uniform float uLevel; uniform float uEnergy;
      varying float vN; varying vec3 vNormalW; varying vec3 vView;
      // classic simplex-ish value noise (cheap, smooth)
      vec3 mod289(vec3 x){return x-floor(x*(1.0/289.0))*289.0;}
      vec4 mod289(vec4 x){return x-floor(x*(1.0/289.0))*289.0;}
      vec4 permute(vec4 x){return mod289(((x*34.0)+1.0)*x);}
      vec4 taylorInvSqrt(vec4 r){return 1.79284291400159-0.85373472095314*r;}
      float snoise(vec3 v){
        const vec2 C=vec2(1.0/6.0,1.0/3.0); const vec4 D=vec4(0.0,0.5,1.0,2.0);
        vec3 i=floor(v+dot(v,C.yyy)); vec3 x0=v-i+dot(i,C.xxx);
        vec3 g=step(x0.yzx,x0.xyz); vec3 l=1.0-g; vec3 i1=min(g.xyz,l.zxy); vec3 i2=max(g.xyz,l.zxy);
        vec3 x1=x0-i1+C.xxx; vec3 x2=x0-i2+C.yyy; vec3 x3=x0-D.yyy;
        i=mod289(i);
        vec4 p=permute(permute(permute(i.z+vec4(0.0,i1.z,i2.z,1.0))+i.y+vec4(0.0,i1.y,i2.y,1.0))+i.x+vec4(0.0,i1.x,i2.x,1.0));
        float n_=0.142857142857; vec3 ns=n_*D.wyz-D.xzx;
        vec4 j=p-49.0*floor(p*ns.z*ns.z); vec4 x_=floor(j*ns.z); vec4 y_=floor(j-7.0*x_);
        vec4 x=x_*ns.x+ns.yyyy; vec4 y=y_*ns.x+ns.yyyy; vec4 h=1.0-abs(x)-abs(y);
        vec4 b0=vec4(x.xy,y.xy); vec4 b1=vec4(x.zw,y.zw);
        vec4 s0=floor(b0)*2.0+1.0; vec4 s1=floor(b1)*2.0+1.0; vec4 sh=-step(h,vec4(0.0));
        vec4 a0=b0.xzyw+s0.xzyw*sh.xxyy; vec4 a1=b1.xzyw+s1.xzyw*sh.zzww;
        vec3 p0=vec3(a0.xy,h.x); vec3 p1=vec3(a0.zw,h.y); vec3 p2=vec3(a1.xy,h.z); vec3 p3=vec3(a1.zw,h.w);
        vec4 norm=taylorInvSqrt(vec4(dot(p0,p0),dot(p1,p1),dot(p2,p2),dot(p3,p3)));
        p0*=norm.x;p1*=norm.y;p2*=norm.z;p3*=norm.w;
        vec4 m=max(0.6-vec4(dot(x0,x0),dot(x1,x1),dot(x2,x2),dot(x3,x3)),0.0); m=m*m;
        return 42.0*dot(m*m,vec4(dot(p0,x0),dot(p1,x1),dot(p2,x2),dot(p3,x3)));
      }
      void main(){
        float amp=0.14+uEnergy*0.22+uLevel*0.55;
        float n=snoise(normal*1.6+vec3(uTime*0.35));
        n+=0.5*snoise(normal*3.2+vec3(uTime*0.6+10.0));
        vN=n;
        vec3 pos=position+normal*n*amp;
        vec4 mv=modelViewMatrix*vec4(pos,1.0);
        vNormalW=normalize(mat3(modelViewMatrix)*normal);
        vView=normalize(-mv.xyz);
        gl_Position=projectionMatrix*mv;
      }`,
    fragmentShader: `
      uniform vec3 uBase; uniform vec3 uHot; uniform float uLevel;
      varying float vN; varying vec3 vNormalW; varying vec3 vView;
      void main(){
        float fres=pow(1.0-max(dot(vNormalW,vView),0.0),2.6);
        vec3 col=mix(uBase,uHot,clamp(vN*0.6+0.4+uLevel*0.4,0.0,1.0));
        col+=uHot*fres*0.9;
        float a=0.30+fres*0.4;
        gl_FragColor=vec4(col,a);
      }`,
    depthWrite: false,
    blending: THREE.AdditiveBlending,
  });
  group.add(new THREE.Mesh(sphereGeo, sphereMat));

  // ---- fresnel glow shell (slightly larger, back side)
  const shellMat = new THREE.ShaderMaterial({
    uniforms,
    transparent: true, side: THREE.BackSide, depthWrite: false, blending: THREE.AdditiveBlending,
    vertexShader: `varying vec3 vN; varying vec3 vV;
      void main(){ vec4 mv=modelViewMatrix*vec4(position,1.0);
        vN=normalize(mat3(modelViewMatrix)*normal); vV=normalize(-mv.xyz);
        gl_Position=projectionMatrix*mv; }`,
    fragmentShader: `uniform vec3 uHot; uniform float uLevel; varying vec3 vN; varying vec3 vV;
      void main(){ float f=pow(1.0-max(dot(vN,vV),0.0),3.4);
        gl_FragColor=vec4(uHot*f*(0.8+uLevel*0.6),f*0.5); }`,
  });
  const shell = new THREE.Mesh(new THREE.IcosahedronGeometry(1.8, 16), shellMat);
  group.add(shell);

  // ---- particle halo
  const COUNT = 900;
  const pGeo = new THREE.BufferGeometry();
  const pos = new Float32Array(COUNT * 3);
  const seed = new Float32Array(COUNT);
  for (let i = 0; i < COUNT; i++) {
    const r = 2.15 + Math.random() * 0.9;
    const t = Math.random() * Math.PI * 2, p = Math.acos(2 * Math.random() - 1);
    pos[i*3] = r*Math.sin(p)*Math.cos(t); pos[i*3+1] = r*Math.sin(p)*Math.sin(t); pos[i*3+2] = r*Math.cos(p);
    seed[i] = Math.random();
  }
  pGeo.setAttribute("position", new THREE.BufferAttribute(pos, 3));
  pGeo.setAttribute("seed", new THREE.BufferAttribute(seed, 1));
  const pMat = new THREE.ShaderMaterial({
    uniforms, transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
    vertexShader: `uniform float uTime; uniform float uLevel; attribute float seed; varying float vS;
      void main(){ vS=seed; vec3 p=position;
        float a=uTime*(0.15+seed*0.35); // gentle orbit
        mat3 R=mat3(cos(a),0.0,sin(a), 0.0,1.0,0.0, -sin(a),0.0,cos(a));
        p=R*p; float breathe=1.0+sin(uTime*1.5+seed*6.28)*0.04+uLevel*0.14;
        p*=breathe; vec4 mv=modelViewMatrix*vec4(p,1.0);
        gl_PointSize=clamp((0.8+seed*1.6+uLevel*1.6)*(90.0/-mv.z),0.5,7.0); gl_Position=projectionMatrix*mv; }`,
    fragmentShader: `uniform vec3 uHot; uniform vec3 uBase; varying float vS;
      void main(){ vec2 c=gl_PointCoord-0.5; float d=length(c); if(d>0.5)discard;
        float g=smoothstep(0.5,0.0,d); vec3 col=mix(uBase,uHot,vS);
        gl_FragColor=vec4(col,g*0.45); }`,
  });
  group.add(new THREE.Points(pGeo, pMat));

  // ---- three thin rings at different tilts
  function ring(radius, tilt, tilt2) {
    const g = new THREE.TorusGeometry(radius, 0.008, 8, 160);
    const m = new THREE.MeshBasicMaterial({ transparent: true, opacity: 0.5,
      blending: THREE.AdditiveBlending, depthWrite: false });
    const mesh = new THREE.Mesh(g, m); mesh.rotation.x = tilt; mesh.rotation.y = tilt2;
    mesh.userData.spin = 0.1 + Math.random() * 0.2; group.add(mesh); return mesh;
  }
  const rings = [ring(2.35, 1.2, 0.3), ring(2.7, -0.6, 0.8), ring(3.05, 0.4, -0.5)];

  // ---- state / level handling
  let target = { base: new THREE.Color(), hot: new THREE.Color(), energy: 0.2 };
  let levelTarget = 0;
  function applyState(name) {
    const p = PALETTE[name] || PALETTE.idle;
    target.base.setRGB(p.base[0], p.base[1], p.base[2]);
    target.hot.setRGB(p.hot[0], p.hot[1], p.hot[2]);
    target.energy = ({ idle: 0.12, listening: 0.4, thinking: 0.85, speaking: 0.6, acting: 0.7, boot: 0.3 })[name] ?? 0.2;
    const c = `rgb(${p.hot.map(v => Math.round(v * 255)).join(",")})`;
    rings.forEach(r => r.material.color.set(c));
  }
  applyState("boot");

  // ---- resize
  function resize() {
    const w = window.innerWidth, h = window.innerHeight;
    renderer.setSize(w, h, false); camera.aspect = w / h; camera.updateProjectionMatrix();
  }
  window.addEventListener("resize", resize); resize();

  // ---- loop
  const clock = new THREE.Clock();
  function frame() {
    const dt = Math.min(clock.getDelta(), 0.05);
    uniforms.uTime.value += dt;
    uniforms.uLevel.value += (levelTarget - uniforms.uLevel.value) * Math.min(1, dt * 12);
    uniforms.uEnergy.value += (target.energy - uniforms.uEnergy.value) * Math.min(1, dt * 3);
    uniforms.uBase.value.lerp(target.base, Math.min(1, dt * 3));
    uniforms.uHot.value.lerp(target.hot, Math.min(1, dt * 3));
    group.rotation.y += dt * (0.12 + uniforms.uEnergy.value * 0.15);
    group.rotation.x = Math.sin(uniforms.uTime.value * 0.2) * 0.12;
    rings.forEach((r, i) => { r.rotation.z += dt * r.userData.spin * (i % 2 ? 1 : -1); });
    const breathe = 1 + Math.sin(uniforms.uTime.value * 1.1) * 0.015;
    group.scale.setScalar(breathe);
    renderer.render(scene, camera);
    requestAnimationFrame(frame);
  }
  frame();

  window.JarvisCore = {
    setState: applyState,
    setLevel: (v) => { levelTarget = Math.max(0, Math.min(1, v)); },
    pulse: () => { levelTarget = Math.min(1, levelTarget + 0.5); },
  };
})();
