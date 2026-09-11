
from __future__ import annotations
import io, json, os, re, zipfile, difflib
from dataclasses import dataclass, asdict
from typing import Dict, List, Optional, Tuple
import numpy as np
from PIL import Image

def _norm(s: str) -> str:
    s=(s or "").lower().replace("&"," and ")
    return re.sub(r"[^a-z0-9]+","",s)

def _parse_art_name(filename: str):
    stem=filename[:-4] if filename.lower().endswith(".png") else filename
    sex_art=None
    if stem.endswith("_sf"):
        sex_art="female"; stem=stem[:-3]
    elif stem.endswith("_sm"):
        sex_art="male"; stem=stem[:-3]
    game=None
    if stem.endswith("_ASA"):
        game="ASA"; display=stem[:-4]
    elif stem.endswith("_ASE"):
        game="ASE"; display=stem[:-4]
    else:
        display=stem
    return display, game, sex_art

@dataclass
class SpeciesAsset:
    species: str
    game: str
    base_file: str
    mask_file: str
    visible_regions: List[int]
    sex_art: Optional[str]=None

@dataclass
class ResolveResult:
    requested_species: str
    canonical_species: str
    sex_requested: Optional[str]
    asset: SpeciesAsset
    match_type: str
    alternatives: List[str]

class SpeciesIntelligence:
    def __init__(self, species_images_zip: str, asb_zip: str):
        self.species_images_zip=species_images_zip
        self.asb_zip=asb_zip
        self._assets=self._load_assets()
        self._species_regions, self._color_defs=self._load_asb_reference()
        self._canonical_names=sorted(set(a["species"] for a in self._assets))

    def _load_assets(self):
        with zipfile.ZipFile(self.species_images_zip) as z:
            names=[n for n in z.namelist() if "/images/" in n and n.lower().endswith(".png")]
        masks={os.path.basename(n)[:-6]+".png":n for n in names if os.path.basename(n).lower().endswith("_m.png")}
        assets=[]
        for n in names:
            fn=os.path.basename(n)
            if fn.lower().endswith("_m.png"): continue
            m=masks.get(fn)
            if not m: continue
            display,game,sex_art=_parse_art_name(fn)
            if game!="ASA": continue
            assets.append({
                "species":display,
                "norm":_norm(display),
                "game":game,
                "base_path":n,
                "mask_path":m,
                "base_file":fn,
                "mask_file":os.path.basename(m),
                "sex_art":sex_art
            })
        return assets

    def _load_asb_reference(self):
        with zipfile.ZipFile(self.asb_zip) as z:
            vals_path=next(n for n in z.namelist() if n.endswith("/ARKBreedingStats/json/values/values.json"))
            vals=json.loads(z.read(vals_path))
        regions={}
        for sp in vals.get("species",[]):
            name=sp.get("name")
            colors=sp.get("colors")
            if not name or not isinstance(colors,list): continue
            regions[_norm(name)]=[i for i,r in enumerate(colors[:6]) if r is not None]
        return regions, vals.get("colorDefinitions",[])


    def _mask_visible_regions(self, asset_record)->List[int]:
        """Fallback for variants missing ASB region metadata.
        Uses strong mask signal to avoid anti-aliasing false positives."""
        with zipfile.ZipFile(self.species_images_zip) as z:
            raw=z.read(asset_record["mask_path"])
        im=Image.open(io.BytesIO(raw)).convert("RGB")
        m=np.array(im).astype(np.int16)
        r,g,b=m[:,:,0],m[:,:,1],m[:,:,2]
        formulas=[
            np.maximum(0,r-g-b), np.maximum(0,g-r-b), np.maximum(0,b-r-g),
            np.minimum(g,b), np.minimum(r,g), np.minimum(r,b)
        ]
        result=[]
        for i,v in enumerate(formulas):
            # Strong canonical-region coverage. Threshold chosen to reject
            # anti-aliased cross-channel noise seen in real ASB masks.
            if float((v>=128).mean()) >= 0.003:
                result.append(i)
        return result

    def color_name(self, color_id:int)->str:
        if not isinstance(color_id,int) or color_id<1 or color_id>len(self._color_defs):
            return "Unknown"
        return self._color_defs[color_id-1][0]

    def color_rgb(self, color_id:int)->Tuple[int,int,int]:
        if not isinstance(color_id,int) or color_id<1 or color_id>len(self._color_defs):
            return (128,128,128)
        row=self._color_defs[color_id-1]
        vals=row[1] if len(row)>1 and isinstance(row[1],list) else row[1:4]
        vals=vals[:3]
        if len(vals)==3 and all(isinstance(v,(int,float)) for v in vals):
            return tuple(max(0,min(255,round(v*255))) for v in vals)
        return (128,128,128)

    def suggest(self, query:str, limit:int=6)->List[str]:
        qn=_norm(query)
        # Exact/prefix/contains first, then fuzzy.
        scored=[]
        for name in self._canonical_names:
            nn=_norm(name)
            score=0
            if nn==qn: score=1000
            elif nn.startswith(qn) or qn.startswith(nn): score=800
            elif qn in nn or nn in qn: score=650
            else:
                score=int(difflib.SequenceMatcher(None,qn,nn).ratio()*500)
            scored.append((score,name))
        out=[]
        for score,name in sorted(scored,key=lambda x:(-x[0],x[1]))[:limit*3]:
            if name not in out:
                out.append(name)
            if len(out)>=limit: break
        return out

    def resolve_detailed(self, species:str, sex:Optional[str]=None)->ResolveResult:
        qn=_norm(species)
        exact=[a for a in self._assets if a["norm"]==qn]
        match_type="exact"
        canonical=species
        if not exact:
            suggestions=self.suggest(species)
            if not suggestions:
                raise KeyError(f"No ASA species artwork found for {species}")
            top=suggestions[0]
            # Only auto-resolve conservative near matches.
            ratio=difflib.SequenceMatcher(None, qn, _norm(top)).ratio()
            if ratio < 0.86:
                raise KeyError(f"No confident ASA species match for {species}. Suggestions: {', '.join(suggestions)}")
            exact=[a for a in self._assets if a["norm"]==_norm(top)]
            canonical=top
            match_type="fuzzy"
        else:
            canonical=exact[0]["species"]

        sex=(sex or "").lower() or None
        chosen=None
        if sex:
            chosen=next((a for a in exact if a["sex_art"]==sex),None)
        if chosen is None:
            chosen=next((a for a in exact if a["sex_art"] is None),None)
        if chosen is None:
            chosen=exact[0]

        visible=self._species_regions.get(_norm(canonical))
        if visible is None or len(visible)==0:
            visible=self._mask_visible_regions(chosen)
        asset=SpeciesAsset(
            species=canonical, game="ASA",
            base_file=chosen["base_file"], mask_file=chosen["mask_file"],
            visible_regions=visible, sex_art=chosen["sex_art"]
        )
        alts=[n for n in self.suggest(species,6) if n!=canonical][:5]
        return ResolveResult(species,canonical,sex,asset,match_type,alts)

    def resolve(self, species:str, sex:Optional[str]=None)->SpeciesAsset:
        return self.resolve_detailed(species,sex).asset

    def region_record(self, species:str, region_ids:Dict[int,int], sex:Optional[str]=None):
        asset=self.resolve(species,sex)
        return [{
            "region":r,
            "color_id":region_ids.get(r),
            "color_name":self.color_name(region_ids[r]) if r in region_ids else None,
            "rgb":self.color_rgb(region_ids[r]) if r in region_ids else None,
            "visible_on_species":r in asset.visible_regions
        } for r in range(6)]

    @staticmethod
    def genetic_level(stats:Dict[str,Dict[str,int]])->int:
        return 1+sum(int(v.get("wild",0) or 0)+int(v.get("mutations",0) or 0) for v in stats.values())

    def _read_asset(self, filename:str)->Image.Image:
        with zipfile.ZipFile(self.species_images_zip) as z:
            member=next(n for n in z.namelist() if n.endswith("/images/"+filename))
            return Image.open(io.BytesIO(z.read(member))).copy()

    def render(self, species:str, region_ids:Dict[int,int], sex:Optional[str]=None)->Image.Image:
        asset=self.resolve(species,sex)
        base=self._read_asset(asset.base_file).convert("RGBA")
        mask=self._read_asset(asset.mask_file).convert("RGB")
        if mask.size!=base.size:
            mask=mask.resize(base.size,Image.Resampling.BICUBIC)
        a=np.array(base); m=np.array(mask).astype(np.int16); out=a[:,:,:3].astype(np.float32)
        r,g,b=m[:,:,0],m[:,:,1],m[:,:,2]
        region_mask={
            0:np.maximum(0,r-g-b),
            1:np.maximum(0,g-r-b),
            2:np.maximum(0,b-r-g),
            3:np.minimum(g,b),
            4:np.minimum(r,g),
            5:np.minimum(r,b),
        }
        for region in range(6):
            if region not in region_ids: continue
            tint=np.array(self.color_rgb(region_ids[region]),dtype=np.int16)
            opacity=region_mask[region].astype(np.float32)/255.0
            mixed=np.clip(out.astype(np.int16)+tint-128,0,255).astype(np.float32)
            out=opacity[:,:,None]*mixed+(1-opacity[:,:,None])*out
        rgba=np.dstack([np.clip(out,0,255).astype(np.uint8),a[:,:,3]])
        return Image.fromarray(rgba,"RGBA")

    def manifest(self):
        records=[]
        seen=set()
        for a in self._assets:
            k=(a["norm"],a["sex_art"],a["base_file"])
            if k in seen: continue
            seen.add(k)
            records.append(asdict(self.resolve(a["species"],a["sex_art"])))
        return sorted(records,key=lambda x:(x["species"].lower(),x["sex_art"] or ""))
